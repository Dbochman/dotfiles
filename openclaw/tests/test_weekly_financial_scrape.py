"""Regression tests for deterministic weekly financial scrape orchestration."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, call, patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
MODULE_PATH = BIN_DIR / "weekly-financial-scrape.py"
SPEC = importlib.util.spec_from_file_location("weekly_financial_scrape", MODULE_PATH)
weekly_financial_scrape = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = weekly_financial_scrape
SPEC.loader.exec_module(weekly_financial_scrape)


class WeeklyFinancialScrapeTests(unittest.TestCase):
    RUN_ID = "11111111-2222-3333-4444-555555555555"
    TESLA_EMAIL = "cabin-owner@example.invalid"
    BASE_ENV = {
        "HOME": str(Path.home()),
        "LANG": "en_US.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
    }
    UNRELATED_SECRET_ENV = {
        "AWS_SECRET_ACCESS_KEY": "unrelated-aws-secret",
        "HOME_ASSISTANT_TOKEN": "unrelated-household-token",
        "NEST_CLIENT_SECRET": "unrelated-nest-secret",
        "OPENAI_API_KEY": "unrelated-openai-secret",
        "OPENCLAW_GATEWAY_TOKEN": "unrelated-gateway-secret",
        "PINCHTAB_TOKEN": "unrelated-browser-token",
        "PLAID_SECRET": "unrelated-plaid-secret",
        "RING_REFRESH_TOKEN": "unrelated-ring-secret",
        "SAFE_PARENT_VALUE": "must-not-be-inherited",
    }
    BOA_SIGN_ON_URL = (
        "https://secure.bankofamerica.com/login/sign-in/"
        "signOnV2Screen.go?request_locale=en-us"
    )
    CREDENTIAL_STORE = {
        profile: (f"private-{profile}-user", f"private-{profile}-password")
        for profile in weekly_financial_scrape.FINANCE_CREDENTIAL_KEYS
    }

    @classmethod
    def credential_parent_environment(cls):
        environment = {
            **cls.BASE_ENV,
            **cls.UNRELATED_SECRET_ENV,
            "OP_SERVICE_ACCOUNT_TOKEN": "stale-parent-token",
            "SCRAPER_USER": "stale-parent-user",
            "SCRAPER_PW": "stale-parent-password",
        }
        for profile, (username_key, password_key) in (
            weekly_financial_scrape.FINANCE_CREDENTIAL_KEYS.items()
        ):
            username, password = cls.CREDENTIAL_STORE[profile]
            environment[username_key] = username
            environment[password_key] = password
        return environment

    @classmethod
    def write_credential_cache(cls, path, store=None, mode=0o600):
        store = cls.CREDENTIAL_STORE if store is None else store
        payload = {
            profile: {"username": username, "password": password}
            for profile, (username, password) in store.items()
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)

    @staticmethod
    def write_provider_modes(path, modes=None, mode=0o600):
        selected = modes or {
            source: "auto"
            for source in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        payload = {"contract": 2, "modes": selected}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)

    @classmethod
    def write_repo_dotenv(
        cls,
        repo,
        *,
        content=None,
        mode=0o600,
        parent_mode=0o755,
    ):
        repo.mkdir(parents=True, exist_ok=True)
        repo.chmod(parent_mode)
        payload = (
            f"TESLA_EMAIL={cls.TESLA_EMAIL}\n"
            "PLAID_CLIENT_ID=unrelated-fixture-value\n"
            if content is None
            else content
        )
        path = repo / ".env"
        path.write_text(payload, encoding="utf-8")
        path.chmod(mode)
        return path

    @staticmethod
    def source_named(name):
        return next(
            source for source in weekly_financial_scrape.SOURCES
            if source.name == name
        )

    @staticmethod
    def status_line(source, path=None, **overrides):
        if path is None:
            path = "direct_api" if source == "tesla_solar" else "direct_http"
        payload = {
            "contract": weekly_financial_scrape.SCRAPER_CONTRACT_VERSION,
            "source": source,
            "path": path,
            **overrides,
        }
        return "FINANCE_SCRAPER_STATUS " + json.dumps(
            payload,
            separators=(",", ":"),
        )

    @staticmethod
    def contract_result():
        return weekly_financial_scrape.CommandResult(
            0,
            stdout=weekly_financial_scrape.SCRAPER_CONTRACT_LINE + "\n",
        )

    @staticmethod
    def manifest_result():
        return weekly_financial_scrape.CommandResult(
            0,
            stdout=weekly_financial_scrape.SCRAPER_MANIFEST_LINE + "\n",
        )

    @classmethod
    def contract_preflight_results(cls):
        return [cls.contract_result(), cls.manifest_result()]

    @staticmethod
    def capture_stdout(function, *args, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = function(*args, **kwargs)
        return result, output.getvalue()

    def test_dry_run_lists_sources_without_running_commands_or_credentials(self):
        with (
            patch.object(sys, "argv", [str(MODULE_PATH), "--dry-run"]),
            patch.object(weekly_financial_scrape, "run_command") as run_command,
            patch.object(weekly_financial_scrape, "credentials_for") as credentials,
            patch.object(weekly_financial_scrape.subprocess, "Popen") as popen,
        ):
            returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 0)
        self.assertEqual(
            json.loads(output),
            {
                "status": "dry_run",
                "sources": [
                    source.name for source in weekly_financial_scrape.SOURCES
                ] + ["boa"],
            },
        )
        run_command.assert_not_called()
        credentials.assert_not_called()
        popen.assert_not_called()

    def test_all_scheduled_source_commands_pin_wrapper_contract_v2(self):
        expected = weekly_financial_scrape.SCRAPER_WRAPPER_CONTRACT_ARGS
        self.assertEqual(expected, ("--wrapper-contract", "2"))
        for source in weekly_financial_scrape.SOURCES:
            with self.subTest(source=source.name):
                self.assertEqual(source.scrape_args.count("--wrapper-contract"), 1)
                index = source.scrape_args.index("--wrapper-contract")
                self.assertEqual(source.scrape_args[index:index + 2], expected)

    def test_standard_source_does_not_reauth_unrecognized_failure(self):
        source = self.source_named("eversource")
        failed = weekly_financial_scrape.CommandResult(
            1,
            stderr="connection reset while loading account activity",
        )

        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=failed,
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
            )

        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(
            run_command.call_args.args[0],
            (*source.scrape_args, "--run-id", self.RUN_ID),
        )
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_direct_only_mode_is_passed_and_rejects_browser_path(self):
        source = self.source_named("eversource")
        modes = {
            name: "auto"
            for name in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        modes["eversource"] = "direct_only"
        responses = iter([
            weekly_financial_scrape.CommandResult(
                0,
                stdout=self.status_line("eversource", "browser_recovery"),
            ),
        ])
        with patch.object(
            weekly_financial_scrape,
            "run_command",
            side_effect=lambda *_args, **_kwargs: next(responses),
        ) as run_command:
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                modes,
            )

        self.assertIn("--direct-only", run_command.call_args_list[0].args[0])
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["import"], "skipped")
        self.assertEqual(result["path"], "mode_mismatch")

    def test_direct_only_auth_failure_never_reauths_or_selects_credentials(self):
        source = self.source_named("eversource")
        modes = {
            name: "auto"
            for name in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        modes["eversource"] = "direct_only"
        failed = weekly_financial_scrape.CommandResult(
            1,
            stderr="ERROR: Eversource authentication required",
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=failed,
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                modes,
            )

        self.assertEqual(run_command.call_count, 1)
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["reauth"], "not_needed")

    def test_boa_direct_only_never_verifies_bootstraps_or_reauths(self):
        modes = {
            name: "auto"
            for name in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        modes["boa"] = "direct_only"
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=weekly_financial_scrape.CommandResult(1),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
            ) as ensure_tab,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                None,
                modes,
            )

        self.assertEqual(run_command.call_count, 1)
        arguments = run_command.call_args.args[0]
        self.assertIn("--direct-only", arguments)
        self.assertNotIn("--boa-pinchtab-instance", arguments)
        credentials.assert_not_called()
        ensure_tab.assert_not_called()
        self.assertEqual(result["verify_auth"], "not_needed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_boa_direct_only_skips_profile_preflight_for_whole_run(self):
        modes = {
            name: "auto"
            for name in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        modes["boa"] = "direct_only"
        boa_result = {
            "source": "boa",
            "scrape": "failed",
            "verify_auth": "not_needed",
            "tab_bootstrap": "not_needed",
            "reauth": "not_needed",
            "import": "skipped",
            "path": "not_observed",
        }
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            python = repo / "venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(weekly_financial_scrape, "SOURCES", ()),
                patch.object(
                    weekly_financial_scrape,
                    "scraper_contract_preflight",
                    return_value={"status": "contract_ok"},
                ),
                patch.object(
                    weekly_financial_scrape,
                    "load_provider_modes",
                    return_value=modes,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "credential_preflight",
                    return_value=(
                        self.BASE_ENV,
                        self.CREDENTIAL_STORE,
                        {"status": "preflight_ok"},
                    ),
                ),
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_profile",
                ) as profile,
                patch.object(
                    weekly_financial_scrape,
                    "run_boa",
                    return_value=boa_result,
                ) as run_boa,
                patch.object(
                    weekly_financial_scrape,
                    "finish_run",
                    return_value=True,
                ),
            ):
                self.assertEqual(
                    weekly_financial_scrape._execute_run(self.RUN_ID),
                    1,
                )

        profile.assert_not_called()
        self.assertIsNone(run_boa.call_args.args[3])
        self.assertEqual(run_boa.call_args.args[4], modes)

    def test_only_exact_source_authentication_lines_are_recognized(self):
        for source, marker in weekly_financial_scrape.AUTH_FAILURE_LINES.items():
            with self.subTest(source=source):
                self.assertTrue(
                    weekly_financial_scrape.is_auth_failure(
                        weekly_financial_scrape.CommandResult(1, stderr=marker),
                        source,
                    )
                )
        for stderr in (
            "PennyMac API failed: authentication required",
            "zsh: command not found: scraper",
            "service-account token unavailable",
            "authentication service unavailable",
        ):
            with self.subTest(stderr=stderr):
                self.assertFalse(
                    weekly_financial_scrape.is_auth_failure(
                        weekly_financial_scrape.CommandResult(1, stderr=stderr),
                        "pennymac",
                    )
                )

    def test_bwsc_requires_the_exact_fixed_auth_line(self):
        exact = weekly_financial_scrape.CommandResult(
            1,
            stderr="ERROR: BWSC authentication required",
        )
        self.assertTrue(weekly_financial_scrape.is_auth_failure(exact, "bwsc"))

        for stderr in (
            "prefix ERROR: BWSC authentication required",
            "ERROR: BWSC authentication required extra",
            "provider says authentication required",
        ):
            with self.subTest(stderr=stderr):
                self.assertFalse(
                    weekly_financial_scrape.is_auth_failure(
                        weekly_financial_scrape.CommandResult(1, stderr=stderr),
                        "bwsc",
                    )
                )

    def test_credentials_are_scoped_to_one_reauth_child(self):
        credential_env = weekly_financial_scrape.credentials_for(
            "eversource",
            self.credential_parent_environment(),
            self.CREDENTIAL_STORE,
        )

        self.assertEqual(
            credential_env["SCRAPER_USER"],
            self.CREDENTIAL_STORE["eversource"][0],
        )
        self.assertEqual(
            credential_env["SCRAPER_PW"],
            self.CREDENTIAL_STORE["eversource"][1],
        )
        self.assertEqual(
            set(credential_env),
            set(self.BASE_ENV) | set(weekly_financial_scrape.SCRAPER_CREDENTIAL_KEYS),
        )
        for key in (
            "OP_SERVICE_ACCOUNT_TOKEN",
            *self.UNRELATED_SECRET_ENV,
            *weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS,
        ):
            self.assertNotIn(key, credential_env)

    def test_python_child_boundary_allows_only_runtime_keys_and_guarded_credentials(self):
        process = Mock(pid=1234, returncode=0)
        parent = self.credential_parent_environment()
        guarded = weekly_financial_scrape.credentials_for(
            "eversource",
            parent,
            self.CREDENTIAL_STORE,
        )
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ) as popen,
            patch.object(
                weekly_financial_scrape,
                "_read_bounded_child_output",
                return_value=(b"", b""),
            ),
            patch.object(
                weekly_financial_scrape,
                "_stop_process_group",
            ) as stop_group,
        ):
            weekly_financial_scrape.run_command(("ordinary.py",), parent)
            weekly_financial_scrape.run_command(("reauth.py",), guarded)

        ordinary_env = popen.call_args_list[0].kwargs["env"]
        reauth_env = popen.call_args_list[1].kwargs["env"]
        self.assertEqual(
            stop_group.call_args_list,
            [call(process), call(process)],
        )
        expected_runtime = {
            **self.BASE_ENV,
            weekly_financial_scrape.PYTHON_DOTENV_DISABLED_KEY: "1",
        }
        self.assertEqual(ordinary_env, expected_runtime)
        self.assertEqual(
            set(reauth_env),
            set(expected_runtime)
            | set(weekly_financial_scrape.SCRAPER_CREDENTIAL_KEYS),
        )
        self.assertEqual(
            reauth_env["SCRAPER_USER"],
            self.CREDENTIAL_STORE["eversource"][0],
        )
        self.assertEqual(
            reauth_env["SCRAPER_PW"],
            self.CREDENTIAL_STORE["eversource"][1],
        )
        for child_env in (ordinary_env, reauth_env):
            for key in (
                "OP_SERVICE_ACCOUNT_TOKEN",
                *self.UNRELATED_SECRET_ENV,
                *weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS,
            ):
                self.assertNotIn(key, child_env)

    def test_dedicated_cache_is_loaded_and_parent_credentials_are_scrubbed(self):
        parent = self.credential_parent_environment()
        parent["FINANCE_EVERSOURCE_STORAGE_STATE"] = "/unsafe/override"
        parent["FINANCE_TESLA_TOKEN_CACHE"] = "/unsafe/cache"
        with tempfile.TemporaryDirectory() as tempdir:
            cache = Path(tempdir) / "scraper-credentials.json"
            self.write_credential_cache(cache)
            store = weekly_financial_scrape.load_credential_store(cache)

        child = weekly_financial_scrape.scrub_child_environment(parent)

        self.assertEqual(store, self.CREDENTIAL_STORE)
        self.assertEqual(child, self.BASE_ENV)
        for key in (
            "OP_SERVICE_ACCOUNT_TOKEN",
            "SCRAPER_USER",
            "SCRAPER_PW",
            *self.UNRELATED_SECRET_ENV,
            *weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS,
        ):
            self.assertNotIn(key, child)
        self.assertFalse(any(key.startswith("FINANCE_") for key in child))

    def test_provider_modes_default_to_auto_when_optional_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            missing = Path(tempdir) / "missing" / "scraper-modes.json"
            modes = weekly_financial_scrape.load_provider_modes(missing)

        self.assertEqual(
            modes,
            {
                source: "auto"
                for source in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
            },
        )

    def test_provider_modes_require_exact_private_complete_contract(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "state" / "scraper-modes.json"
            modes = {
                source: "auto"
                for source in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
            }
            modes["eversource"] = "direct_only"
            modes["boa"] = "browser_only"
            self.write_provider_modes(path, modes)

            self.assertEqual(
                weekly_financial_scrape.load_provider_modes(path),
                modes,
            )

            path.chmod(0o644)
            with self.assertRaises(weekly_financial_scrape.ProviderModeError):
                weekly_financial_scrape.load_provider_modes(path)

            self.write_provider_modes(path, {**modes, "tesla_solar": "direct_only"})
            with self.assertRaises(weekly_financial_scrape.ProviderModeError):
                weekly_financial_scrape.load_provider_modes(path)

            incomplete = dict(modes)
            incomplete.pop("bwsc")
            self.write_provider_modes(path, incomplete)
            with self.assertRaises(weekly_financial_scrape.ProviderModeError):
                weekly_financial_scrape.load_provider_modes(path)

    def test_provider_modes_append_exact_flags_and_enforce_reported_path(self):
        modes = {
            source: "auto"
            for source in weekly_financial_scrape.PROVIDER_MODE_OPTIONS
        }
        modes["eversource"] = "direct_only"
        modes["boa"] = "browser_only"

        self.assertEqual(
            weekly_financial_scrape.apply_provider_mode(
                ("scrape_eversource.py", "--headless"),
                "eversource",
                modes,
            ),
            ("scrape_eversource.py", "--headless", "--direct-only"),
        )
        self.assertEqual(
            weekly_financial_scrape.apply_provider_mode(
                ("scrape_mortgage.py", "--lender", "boa"),
                "boa",
                modes,
            ),
            (
                "scrape_mortgage.py",
                "--lender",
                "boa",
                "--browser-only",
            ),
        )
        self.assertTrue(
            weekly_financial_scrape.path_matches_provider_mode(
                "eversource", "direct_http", "direct_only"
            )
        )
        self.assertFalse(
            weekly_financial_scrape.path_matches_provider_mode(
                "eversource", "browser_recovery", "direct_only"
            )
        )

    def test_national_grid_sources_share_only_the_national_grid_profile(self):
        electric = self.source_named("national_grid_electric")
        gas = self.source_named("national_grid_gas")

        self.assertEqual(electric.credential_profile, "national_grid")
        self.assertEqual(gas.credential_profile, "national_grid")
        credential_env = weekly_financial_scrape.credentials_for(
            electric.credential_profile,
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(
            credential_env["SCRAPER_USER"],
            self.CREDENTIAL_STORE["national_grid"][0],
        )
        self.assertNotEqual(
            credential_env["SCRAPER_USER"],
            self.CREDENTIAL_STORE["eversource"][0],
        )

    def test_dedicated_cache_rejects_insecure_symlinked_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            cache = root / "scraper-credentials.json"
            self.write_credential_cache(cache, mode=0o644)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(cache)

            cache.chmod(0o600)
            link = root / "linked-credentials.json"
            link.symlink_to(cache)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(link)

            cache.write_text('{"unexpected": true}', encoding="utf-8")
            cache.chmod(0o600)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(cache)

    def test_dedicated_cache_is_bounded_single_link_and_strict_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            cache = root / "scraper-credentials.json"
            self.write_credential_cache(cache)

            hardlink = root / "hardlinked-credentials.json"
            os.link(cache, hardlink)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(cache)
            hardlink.unlink()

            cache.write_bytes(
                b" " * (weekly_financial_scrape.CREDENTIAL_CACHE_MAX_BYTES + 1)
            )
            cache.chmod(0o600)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(cache)

            valid_payload = {
                profile: {"username": username, "password": password}
                for profile, (username, password) in self.CREDENTIAL_STORE.items()
            }
            duplicate = json.dumps(valid_payload).replace(
                '"username":',
                '"username":"duplicate","username":',
                1,
            )
            cache.write_text(duplicate, encoding="utf-8")
            cache.chmod(0o600)
            with self.assertRaises(weekly_financial_scrape.CredentialCacheError):
                weekly_financial_scrape.load_credential_store(cache)

    def test_dedicated_cache_rejects_unbounded_or_nul_credential_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            cache = Path(tempdir) / "scraper-credentials.json"
            for value in (
                "x" * (weekly_financial_scrape.CREDENTIAL_VALUE_MAX_BYTES + 1),
                "bad\x00value",
            ):
                malformed = dict(self.CREDENTIAL_STORE)
                malformed["boa"] = ("user", value)
                self.write_credential_cache(cache, malformed)
                with self.assertRaises(
                    weekly_financial_scrape.CredentialCacheError
                ):
                    weekly_financial_scrape.load_credential_store(cache)

    def test_safe_preflight_fails_before_any_child_when_cache_is_incomplete(self):
        incomplete_store = dict(self.CREDENTIAL_STORE)
        incomplete_store.pop("boa")

        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            python = repo / "venv" / "bin" / "python3"
            cache = Path(tempdir) / "scraper-credentials.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            self.write_credential_cache(cache, incomplete_store)
            with (
                patch.object(sys, "argv", [str(MODULE_PATH), "--preflight"]),
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINANCE_CREDENTIAL_CACHE",
                    cache,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                    side_effect=self.contract_preflight_results(),
                ) as run_command,
                patch.object(weekly_financial_scrape, "ensure_boa_profile") as profile,
                patch.object(weekly_financial_scrape.subprocess, "Popen") as popen,
                patch.dict(
                    os.environ,
                    self.credential_parent_environment(),
                    clear=True,
                ),
            ):
                returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 1)
        self.assertEqual(
            json.loads(output),
            {
                "status": "preflight_failed",
                "reason": "credential_cache_unavailable",
                "missing_profiles": ["boa"],
            },
        )
        self.assertEqual(
            run_command.call_args_list,
            [
                call(
                    weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND,
                    self.BASE_ENV,
                    timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                ),
                call(
                    weekly_financial_scrape.SCRAPER_MANIFEST_COMMAND,
                    self.BASE_ENV,
                    timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                ),
            ],
        )
        profile.assert_not_called()
        popen.assert_not_called()

    def test_missing_tesla_dotenv_fails_preflight_before_any_child_or_source(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"
            python = repo / "venv" / "bin" / "python3"
            status_path = root / "state" / "weekly.json"
            python.parent.mkdir(parents=True)
            python.touch()
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                ) as run_command,
                patch.object(
                    weekly_financial_scrape,
                    "load_credential_store",
                ) as load_credentials,
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_profile",
                ) as ensure_profile,
                patch.object(
                    weekly_financial_scrape,
                    "run_standard_source",
                ) as run_source,
                patch.object(weekly_financial_scrape, "run_boa") as run_boa,
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
                patch.dict(os.environ, self.BASE_ENV, clear=True),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

            self.assertEqual(returncode, 1)
            final = json.loads(output)
            self.assertEqual(final["status"], "preflight_failed")
            self.assertEqual(
                final["reason"],
                "tesla_configuration_unavailable",
            )
            self.assertEqual(final["run_id"], self.RUN_ID)
            self.assertNotIn(self.TESLA_EMAIL, output)
            run_command.assert_not_called()
            load_credentials.assert_not_called()
            ensure_profile.assert_not_called()
            run_source.assert_not_called()
            run_boa.assert_not_called()

    def test_safe_preflight_succeeds_without_browser_or_data_access(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            python = repo / "venv" / "bin" / "python3"
            cache = Path(tempdir) / "scraper-credentials.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            self.write_credential_cache(cache)
            with (
                patch.object(sys, "argv", [str(MODULE_PATH), "--preflight"]),
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINANCE_CREDENTIAL_CACHE",
                    cache,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                    side_effect=self.contract_preflight_results(),
                ) as run_command,
                patch.object(weekly_financial_scrape, "ensure_boa_profile") as profile,
                patch.object(weekly_financial_scrape.subprocess, "Popen") as popen,
                patch.dict(
                    os.environ,
                    self.credential_parent_environment(),
                    clear=True,
                ),
            ):
                returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 0)
        self.assertEqual(
            json.loads(output),
            {
                "status": "preflight_ok",
                "contract": weekly_financial_scrape.SCRAPER_CONTRACT_VERSION,
                "credential_profiles": sorted(self.CREDENTIAL_STORE),
            },
        )
        self.assertEqual(
            run_command.call_args_list,
            [
                call(
                    weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND,
                    self.BASE_ENV,
                    timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                ),
                call(
                    weekly_financial_scrape.SCRAPER_MANIFEST_COMMAND,
                    self.BASE_ENV,
                    timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                ),
            ],
        )
        profile.assert_not_called()
        popen.assert_not_called()

    def test_contract_preflight_requires_one_exact_v2_line(self):
        cases = (
            weekly_financial_scrape.CommandResult(
                1,
                stdout=weekly_financial_scrape.SCRAPER_CONTRACT_LINE + "\n",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout="FINANCE_SCRAPER_CONTRACT 1\n",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=weekly_financial_scrape.SCRAPER_CONTRACT_LINE + "\nextra\n",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=weekly_financial_scrape.SCRAPER_CONTRACT_LINE + "\n",
                stderr="unexpected diagnostic",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=weekly_financial_scrape.SCRAPER_CONTRACT_LINE + "\n",
                stderr="\n",
            ),
            weekly_financial_scrape.CommandResult(
                124,
                timed_out=True,
            ),
        )
        for completed in cases:
            with self.subTest(completed=completed), patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=completed,
            ) as run_command:
                result = weekly_financial_scrape.scraper_contract_preflight(
                    self.BASE_ENV
                )

            self.assertEqual(
                result,
                {
                    "status": "preflight_failed",
                    "reason": "scraper_contract_mismatch",
                },
            )
            run_command.assert_called_once_with(
                weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND,
                self.BASE_ENV,
                timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
            )

    def test_contract_preflight_requires_exact_fleet_capability_manifest(self):
        def changed(mutator):
            payload = json.loads(weekly_financial_scrape.SCRAPER_MANIFEST_LINE)
            mutator(payload)
            return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"

        cases = (
            weekly_financial_scrape.CommandResult(1),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=changed(lambda payload: payload["sources"].pop("bwsc")),
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=changed(
                    lambda payload: payload["sources"]["boa"].__setitem__(
                        "entrypoint", "wrong.py"
                    )
                ),
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=changed(
                    lambda payload: payload["sources"]["eversource"].__setitem__(
                        "import_command", "import-json-water"
                    )
                ),
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=changed(
                    lambda payload: payload["sources"]["pennymac"]["paths"].remove(
                        "direct_http"
                    )
                ),
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=changed(
                    lambda payload: payload["sources"]["national_grid_gas"][
                        "capabilities"
                    ].remove("wrapper_contract_v2")
                ),
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=json.dumps(
                    weekly_financial_scrape.SCRAPER_CAPABILITY_MANIFEST,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=weekly_financial_scrape.SCRAPER_MANIFEST_LINE + "\nextra\n",
            ),
            weekly_financial_scrape.CommandResult(
                0,
                stdout=weekly_financial_scrape.SCRAPER_MANIFEST_LINE + "\n",
                stderr="unexpected diagnostic",
            ),
            weekly_financial_scrape.CommandResult(124, timed_out=True),
        )
        for completed in cases:
            with self.subTest(completed=completed), patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=[self.contract_result(), completed],
            ) as run_command:
                result = weekly_financial_scrape.scraper_contract_preflight(
                    self.BASE_ENV
                )

            self.assertEqual(
                result,
                {
                    "status": "preflight_failed",
                    "reason": "scraper_contract_mismatch",
                },
            )
            self.assertEqual(
                run_command.call_args_list,
                [
                    call(
                        weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND,
                        self.BASE_ENV,
                        timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                    ),
                    call(
                        weekly_financial_scrape.SCRAPER_MANIFEST_COMMAND,
                        self.BASE_ENV,
                        timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
                    ),
                ],
            )

    def test_contract_mismatch_stops_before_credentials_browser_and_data(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"
            python = repo / "venv" / "bin" / "python3"
            status_path = root / "state" / "weekly.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                    return_value=weekly_financial_scrape.CommandResult(
                        0,
                        stdout="FINANCE_SCRAPER_CONTRACT 1\n",
                    ),
                ) as run_command,
                patch.object(
                    weekly_financial_scrape,
                    "load_credential_store",
                ) as load_credentials,
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_profile",
                ) as ensure_profile,
                patch.object(
                    weekly_financial_scrape,
                    "run_standard_source",
                ) as run_source,
                patch.object(weekly_financial_scrape, "run_boa") as run_boa,
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
                patch.dict(os.environ, self.BASE_ENV, clear=True),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

            self.assertEqual(returncode, 1)
            self.assertEqual(
                json.loads(output),
                json.loads(status_path.read_text(encoding="utf-8")),
            )
            final = json.loads(output)
            self.assertEqual(final["status"], "preflight_failed")
            self.assertEqual(final["reason"], "scraper_contract_mismatch")
            self.assertEqual(final["run_id"], self.RUN_ID)
            run_command.assert_called_once_with(
                weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND,
                self.BASE_ENV,
                timeout=weekly_financial_scrape.CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
            )
            load_credentials.assert_not_called()
            ensure_profile.assert_not_called()
            run_source.assert_not_called()
            run_boa.assert_not_called()

    def test_scheduled_run_fails_before_profile_or_scrapers_when_cache_is_incomplete(self):
        incomplete_store = dict(self.CREDENTIAL_STORE)
        incomplete_store.pop("pennymac")
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            python = repo / "venv" / "bin" / "python3"
            cache = Path(tempdir) / "scraper-credentials.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            self.write_credential_cache(cache, incomplete_store)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINANCE_CREDENTIAL_CACHE",
                    cache,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    Path(tempdir) / "status" / "weekly.json",
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                    side_effect=self.contract_preflight_results(),
                ) as run_command,
                patch.object(weekly_financial_scrape, "ensure_boa_profile") as profile,
                patch.object(weekly_financial_scrape.subprocess, "Popen") as popen,
                patch.dict(
                    os.environ,
                    self.credential_parent_environment(),
                    clear=True,
                ),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

        self.assertEqual(returncode, 1)
        final = json.loads(output)
        self.assertEqual(final["missing_profiles"], ["pennymac"])
        self.assertEqual(final["reason"], "credential_cache_unavailable")
        self.assertEqual(final["contract"], 2)
        self.assertEqual(run_command.call_count, 2)
        profile.assert_not_called()
        popen.assert_not_called()

    def test_weekly_helper_has_no_runtime_op_or_token_file_path(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn(".env-token", source)
        self.assertNotIn("op://", source)
        self.assertNotIn("run_op_read", source)
        self.assertNotIn('["op", "read"', source)

    def test_boa_profile_preflight_acquires_only_the_finance_profile(self):
        completed = weekly_financial_scrape.CommandResult(
            0,
            stdout="inst_123abc\t1\n",
        )
        with patch.object(
            weekly_financial_scrape,
            "_run_captured",
            return_value=completed,
        ) as run_captured:
            result = weekly_financial_scrape.ensure_boa_profile(self.BASE_ENV)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.instance_id, "inst_123abc")
        run_captured.assert_called_once_with(
            [
                str(weekly_financial_scrape.PINCHTAB_INSTANCE_HELPER),
                "acquire",
                "finance",
            ],
            self.BASE_ENV,
            weekly_financial_scrape.PROFILE_PREFLIGHT_TIMEOUT_SECONDS,
        )

    def test_boa_profile_preflight_fails_closed_on_bad_helper_results(self):
        cases = (
            (weekly_financial_scrape.CommandResult(1), "failed"),
            (weekly_financial_scrape.CommandResult(124, timed_out=True), "timeout"),
            (weekly_financial_scrape.CommandResult(0, stdout=""), "failed"),
            (weekly_financial_scrape.CommandResult(0, stdout="unexpected\n"), "failed"),
            (
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="inst_one\t0\ninst_two\t0\n",
                ),
                "failed",
            ),
        )
        for completed, expected in cases:
            with self.subTest(completed=completed), patch.object(
                weekly_financial_scrape,
                "_run_captured",
                return_value=completed,
            ):
                result = weekly_financial_scrape.ensure_boa_profile(self.BASE_ENV)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.instance_id)

    def test_boa_profile_preflight_is_part_of_source_health(self):
        result = {
            "source": "boa",
            "scrape": "ok",
            "import": "ok",
            "profile_preflight": "ok",
            "path": "direct_http",
        }
        self.assertTrue(weekly_financial_scrape.result_ok(result))

        result["profile_preflight"] = "failed"
        self.assertFalse(weekly_financial_scrape.result_ok(result))

    def test_boa_children_and_credentials_require_acquired_instance_id(self):
        for instance_id in (None, "", "inst_wrong-format", "inst_123/path"):
            with (
                self.subTest(instance_id=instance_id),
                patch.object(
                    weekly_financial_scrape,
                    "run_command",
                ) as run_command,
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_tab",
                ) as ensure_tab,
                patch.object(
                    weekly_financial_scrape,
                    "credentials_for",
                ) as credentials,
            ):
                result = weekly_financial_scrape.run_boa(
                    self.RUN_ID,
                    self.BASE_ENV,
                    self.CREDENTIAL_STORE,
                    instance_id=instance_id,
                )

            run_command.assert_not_called()
            ensure_tab.assert_not_called()
            credentials.assert_not_called()
            self.assertEqual(result["scrape"], "failed")
            self.assertEqual(result["verify_auth"], "not_needed")
            self.assertEqual(result["tab_bootstrap"], "profile_unavailable")
            self.assertEqual(result["import"], "skipped")

    def test_empty_boa_tab_inventory_opens_only_the_fixed_sign_in_url(self):
        self.assertEqual(
            weekly_financial_scrape.BOA_TAB_BOOTSTRAP_URL,
            self.BOA_SIGN_ON_URL,
        )
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(0, stdout="[]\n"),
                weekly_financial_scrape.CommandResult(0, stdout="tab-created\n"),
            ]
        )
        calls = []

        def fake_run(command, env, timeout, cwd=None):
            calls.append((command, dict(env), timeout, cwd))
            return next(responses)

        with patch.object(
            weekly_financial_scrape,
            "_run_captured",
            side_effect=fake_run,
        ):
            status = weekly_financial_scrape.ensure_boa_tab(
                "inst_123abc",
                self.credential_parent_environment(),
            )

        self.assertEqual(status, "opened")
        self.assertEqual(
            [command for command, _, _, _ in calls],
            [
                [
                    str(weekly_financial_scrape.PINCHTAB_INSTANCE_HELPER),
                    "tabs",
                    "inst_123abc",
                ],
                [
                    str(weekly_financial_scrape.PINCHTAB_INSTANCE_HELPER),
                    "open",
                    "inst_123abc",
                    self.BOA_SIGN_ON_URL,
                ],
            ],
        )
        for _, child_env, timeout, cwd in calls:
            self.assertEqual(
                timeout,
                weekly_financial_scrape.BOA_TAB_OPERATION_TIMEOUT_SECONDS,
            )
            self.assertIsNone(cwd)
            self.assertEqual(child_env, self.BASE_ENV)
            for key in (
                "OP_SERVICE_ACCOUNT_TOKEN",
                "SCRAPER_USER",
                "SCRAPER_PW",
                *self.UNRELATED_SECRET_ENV,
                *weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS,
            ):
                self.assertNotIn(key, child_env)

    def test_existing_exact_https_boa_tab_is_reused_without_navigation(self):
        listed = weekly_financial_scrape.CommandResult(
            0,
            stdout=json.dumps(
                {
                    "tabs": [
                        {
                            "id": "tab-existing",
                            "url": "https://secure.bankofamerica.com/myaccounts/",
                        }
                    ]
                }
            ),
        )
        with patch.object(
            weekly_financial_scrape,
            "_run_captured",
            return_value=listed,
        ) as run_captured:
            status = weekly_financial_scrape.ensure_boa_tab(
                "inst_123abc",
                self.BASE_ENV,
            )

        self.assertEqual(status, "reused")
        run_captured.assert_called_once()

    def test_www_signed_out_inventory_opens_secure_sign_in_instead_of_reusing(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "id": "tab-www-landing",
                                "url": "https://www.bankofamerica.com/",
                            }
                        ]
                    ),
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="tab-created\n",
                ),
            ]
        )
        with patch.object(
            weekly_financial_scrape,
            "_run_captured",
            side_effect=lambda *args, **kwargs: next(responses),
        ) as run_captured:
            status = weekly_financial_scrape.ensure_boa_tab(
                "inst_123abc",
                self.BASE_ENV,
            )

        self.assertEqual(status, "opened")
        self.assertEqual(run_captured.call_count, 2)
        self.assertEqual(
            run_captured.call_args_list[1].args[0],
            [
                str(weekly_financial_scrape.PINCHTAB_INSTANCE_HELPER),
                "open",
                "inst_123abc",
                self.BOA_SIGN_ON_URL,
            ],
        )

    def test_wrong_host_tabs_are_never_reused_for_boa(self):
        hostile_urls = (
            "https://secure.bankofamerica.com.evil.invalid/login",
            "http://secure.bankofamerica.com/myaccounts/",
            "https://user@secure.bankofamerica.com/myaccounts/",
            "https://secure.bankofamerica.com:8443/myaccounts/",
        )
        for hostile_url in hostile_urls:
            with self.subTest(url=hostile_url):
                responses = iter(
                    [
                        weekly_financial_scrape.CommandResult(
                            0,
                            stdout=json.dumps(
                                [{"id": "tab-hostile", "url": hostile_url}]
                            ),
                        ),
                        weekly_financial_scrape.CommandResult(
                            0,
                            stdout="tab-created\n",
                        ),
                    ]
                )
                with patch.object(
                    weekly_financial_scrape,
                    "_run_captured",
                    side_effect=lambda *args, **kwargs: next(responses),
                ) as run_captured:
                    status = weekly_financial_scrape.ensure_boa_tab(
                        "inst_123abc",
                        self.BASE_ENV,
                    )

                self.assertEqual(status, "opened")
                self.assertEqual(run_captured.call_count, 2)
                self.assertEqual(
                    run_captured.call_args_list[1].args[0][-1],
                    self.BOA_SIGN_ON_URL,
                )

    def test_boa_tab_inventory_failures_never_open_a_tab(self):
        cases = (
            (
                weekly_financial_scrape.CommandResult(1, stderr="private"),
                "tab_list_failed",
            ),
            (
                weekly_financial_scrape.CommandResult(124, timed_out=True),
                "tab_list_timeout",
            ),
            (
                weekly_financial_scrape.CommandResult(0, stdout="not-json"),
                "tab_list_failed",
            ),
            (
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout='[{"url": 42}]',
                ),
                "tab_list_failed",
            ),
        )
        for completed, expected in cases:
            with self.subTest(expected=expected), patch.object(
                weekly_financial_scrape,
                "_run_captured",
                return_value=completed,
            ) as run_captured:
                status = weekly_financial_scrape.ensure_boa_tab(
                    "inst_123abc",
                    self.BASE_ENV,
                )

            self.assertEqual(status, expected)
            run_captured.assert_called_once()

    def test_boa_tab_open_failures_fail_closed(self):
        cases = (
            (
                weekly_financial_scrape.CommandResult(1, stderr="private"),
                "open_failed",
            ),
            (
                weekly_financial_scrape.CommandResult(124, timed_out=True),
                "open_timeout",
            ),
            (
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="tab-one\ntab-two\n",
                ),
                "open_failed",
            ),
        )
        for opened, expected in cases:
            with self.subTest(expected=expected):
                responses = iter(
                    [
                        weekly_financial_scrape.CommandResult(0, stdout="[]"),
                        opened,
                    ]
                )
                with patch.object(
                    weekly_financial_scrape,
                    "_run_captured",
                    side_effect=lambda *args, **kwargs: next(responses),
                ) as run_captured:
                    status = weekly_financial_scrape.ensure_boa_tab(
                        "inst_123abc",
                        self.BASE_ENV,
                    )

            self.assertEqual(status, expected)
            self.assertEqual(run_captured.call_count, 2)

    def test_source_path_allowlists_match_contract_v2(self):
        browser_paths = {
            "direct_http": "healthy",
            "browser_recovery": "degraded",
            "browser_only": "degraded",
            "browser_explicit": "degraded",
        }
        self.assertEqual(
            weekly_financial_scrape.SOURCE_PATH_HEALTH,
            {
                "tesla_solar": {"direct_api": "healthy"},
                "eversource": browser_paths,
                "national_grid_electric": browser_paths,
                "national_grid_gas": browser_paths,
                "bwsc": browser_paths,
                "pennymac": browser_paths,
                "boa": {
                    "direct_http": "healthy",
                    "browser_recovery": "degraded",
                    "browser_only": "degraded",
                },
            },
        )

    def test_every_standard_source_propagates_run_id_and_guards_import(self):
        for source in weekly_financial_scrape.SOURCES:
            with self.subTest(source=source.name):
                calls = []

                def fake_run(
                    arguments,
                    env,
                    timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS,
                ):
                    calls.append(arguments)
                    if arguments[0] == "update_data.py":
                        return weekly_financial_scrape.CommandResult(0)
                    return weekly_financial_scrape.CommandResult(
                        0,
                        stdout=self.status_line(source.name),
                    )

                with patch.object(
                    weekly_financial_scrape,
                    "run_command",
                    side_effect=fake_run,
                ):
                    result = weekly_financial_scrape.run_standard_source(
                        source,
                        self.RUN_ID,
                        self.BASE_ENV,
                    )

                self.assertEqual(
                    calls,
                    [
                        (*source.scrape_args, "--run-id", self.RUN_ID),
                        (*source.import_args, "--require-run-id", self.RUN_ID),
                    ],
                )
                self.assertTrue(weekly_financial_scrape.result_ok(result))

    def test_tesla_and_boa_have_no_standard_auth_failure_gate(self):
        tesla = self.source_named("tesla_solar")
        self.assertIsNone(tesla.reauth_args)
        self.assertNotIn("tesla_solar", weekly_financial_scrape.AUTH_FAILURE_LINES)
        self.assertNotIn("boa", weekly_financial_scrape.AUTH_FAILURE_LINES)

        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=weekly_financial_scrape.CommandResult(
                    1,
                    stderr="ERROR: Eversource authentication required",
                ),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                tesla,
                self.RUN_ID,
                self.BASE_ENV,
            )

        run_command.assert_called_once()
        credentials.assert_not_called()
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_standard_source_reauths_once_for_recognized_auth_failure(self):
        source = self.source_named("eversource")
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    1,
                    stderr="ERROR: Eversource authentication required",
                ),
                weekly_financial_scrape.CommandResult(0),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("eversource"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            return next(responses)

        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-user",
            "SCRAPER_PW": "private-password",
        }
        with (
            patch.object(weekly_financial_scrape, "run_command", side_effect=fake_run),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
            )

        credentials.assert_called_once_with(
            source.credential_profile,
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (*source.scrape_args, "--run-id", self.RUN_ID),
                source.reauth_args,
                (*source.scrape_args, "--run-id", self.RUN_ID),
                (*source.import_args, "--require-run-id", self.RUN_ID),
            ],
        )
        self.assertNotIn("SCRAPER_USER", calls[0][1])
        self.assertEqual(calls[1][1]["SCRAPER_USER"], "private-user")
        self.assertEqual(calls[1][1]["SCRAPER_PW"], "private-password")
        self.assertNotIn("SCRAPER_USER", calls[2][1])
        self.assertNotIn("SCRAPER_PW", calls[3][1])
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["reauth"], "ok")
        self.assertEqual(result["import"], "ok")

    def test_standard_reauth_scrubs_selected_credentials_on_error_or_interrupt(self):
        source = self.source_named("eversource")
        failures = (
            RuntimeError("private reauth failure"),
            weekly_financial_scrape.WrapperInterrupted(
                weekly_financial_scrape.signal.SIGTERM
            ),
        )
        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                credential_env = {
                    **self.BASE_ENV,
                    "SCRAPER_USER": "private-user",
                    "SCRAPER_PW": "private-password",
                }
                with (
                    patch.object(
                        weekly_financial_scrape,
                        "run_command",
                        side_effect=[
                            weekly_financial_scrape.CommandResult(
                                1,
                                stderr=(
                                    "ERROR: Eversource authentication required"
                                ),
                            ),
                            failure,
                        ],
                    ),
                    patch.object(
                        weekly_financial_scrape,
                        "credentials_for",
                        return_value=credential_env,
                    ),
                ):
                    with self.assertRaises(type(failure)):
                        weekly_financial_scrape.run_standard_source(
                            source,
                            self.RUN_ID,
                            self.BASE_ENV,
                            self.CREDENTIAL_STORE,
                        )

                self.assertNotIn("SCRAPER_USER", credential_env)
                self.assertNotIn("SCRAPER_PW", credential_env)

    def test_standard_source_only_exposes_selected_credentials_to_reauth_process(self):
        source = self.source_named("eversource")
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    1,
                    stderr="ERROR: Eversource authentication required",
                ),
                weekly_financial_scrape.CommandResult(0),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("eversource"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_captured(command, env, timeout, cwd=None):
            calls.append((tuple(command), dict(env), timeout, cwd))
            return next(responses)

        with patch.object(
            weekly_financial_scrape,
            "_run_captured",
            side_effect=fake_captured,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.credential_parent_environment(),
                self.CREDENTIAL_STORE,
            )

        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["reauth"], "ok")
        self.assertEqual(result["import"], "ok")
        self.assertEqual(len(calls), 4)
        expected_runtime = {
            **self.BASE_ENV,
            weekly_financial_scrape.PYTHON_DOTENV_DISABLED_KEY: "1",
        }
        for index in (0, 2, 3):
            self.assertEqual(calls[index][1], expected_runtime)
        reauth_env = calls[1][1]
        self.assertEqual(
            set(reauth_env),
            set(expected_runtime)
            | set(weekly_financial_scrape.SCRAPER_CREDENTIAL_KEYS),
        )
        self.assertEqual(
            reauth_env["SCRAPER_USER"],
            self.CREDENTIAL_STORE["eversource"][0],
        )
        self.assertEqual(
            reauth_env["SCRAPER_PW"],
            self.CREDENTIAL_STORE["eversource"][1],
        )
        for _, child_env, _, _ in calls:
            for key in (
                "OP_SERVICE_ACCOUNT_TOKEN",
                *self.UNRELATED_SECRET_ENV,
                *weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS,
            ):
                self.assertNotIn(key, child_env)

    def test_tesla_email_loads_from_private_repo_dotenv_without_parent_env(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.dict(os.environ, self.BASE_ENV, clear=True),
            ):
                email = weekly_financial_scrape.load_tesla_email()
                self.assertNotIn(
                    weekly_financial_scrape.TESLA_EMAIL_KEY,
                    os.environ,
                )

        self.assertEqual(email, self.TESLA_EMAIL)

    def test_tesla_dotenv_rejects_missing_duplicate_and_malformed_values(self):
        cases = {
            "missing_key": "PLAID_CLIENT_ID=fixture\n",
            "duplicate_key": (
                f"TESLA_EMAIL={self.TESLA_EMAIL}\n"
                "TESLA_EMAIL=other@example.invalid\n"
            ),
            "controlled_value": "TESLA_EMAIL=owner\t@example.invalid\n",
            "oversized_value": (
                "TESLA_EMAIL="
                + "x" * (weekly_financial_scrape.TESLA_EMAIL_MAX_BYTES + 1)
                + "\n"
            ),
            "malformed_assignment": f"export TESLA_EMAIL={self.TESLA_EMAIL}\n",
        }
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            for name, content in cases.items():
                with self.subTest(name=name):
                    dotenv = self.write_repo_dotenv(repo, content=content)
                    with self.assertRaises(
                        weekly_financial_scrape.TeslaConfigurationError
                    ):
                        weekly_financial_scrape.load_tesla_email(dotenv)

            (repo / ".env").unlink()
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(repo / ".env")

    def test_tesla_dotenv_rejects_unsafe_parent_target_and_link_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"

            dotenv = self.write_repo_dotenv(repo, mode=0o644)
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(dotenv)

            dotenv = self.write_repo_dotenv(repo, parent_mode=0o777)
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(dotenv)

            repo.chmod(0o755)
            dotenv.unlink()
            target = root / "target.env"
            target.write_text(
                f"TESLA_EMAIL={self.TESLA_EMAIL}\n",
                encoding="utf-8",
            )
            target.chmod(0o600)
            dotenv.symlink_to(target)
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(dotenv)

            dotenv.unlink()
            os.link(target, dotenv)
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(dotenv)

            linked_repo = root / "linked-repo"
            linked_repo.symlink_to(repo, target_is_directory=True)
            with self.assertRaises(
                weekly_financial_scrape.TeslaConfigurationError
            ):
                weekly_financial_scrape.load_tesla_email(linked_repo / ".env")

    def test_tesla_identifier_is_scoped_to_scrape_and_dotenv_stays_disabled(self):
        source = self.source_named("tesla_solar")
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("tesla_solar", "direct_api"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_captured(command, env, timeout, cwd=None):
            calls.append((tuple(command), dict(env), timeout, cwd))
            return next(responses)

        with tempfile.TemporaryDirectory() as tempdir:
            dotenv = self.write_repo_dotenv(Path(tempdir) / "repo")
            email = weekly_financial_scrape.load_tesla_email(dotenv)
            tesla_env = weekly_financial_scrape.tesla_source_environment(
                self.credential_parent_environment(),
                email,
            )
            with patch.object(
                weekly_financial_scrape,
                "_run_captured",
                side_effect=fake_captured,
            ):
                result = weekly_financial_scrape.run_standard_source(
                    source,
                    self.RUN_ID,
                    tesla_env,
                )

        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")
        self.assertEqual(len(calls), 2)
        expected_runtime = {
            **self.BASE_ENV,
            weekly_financial_scrape.PYTHON_DOTENV_DISABLED_KEY: "1",
        }
        self.assertEqual(
            calls[0][1],
            {
                **expected_runtime,
                weekly_financial_scrape.TESLA_EMAIL_KEY: self.TESLA_EMAIL,
            },
        )
        self.assertEqual(calls[1][1], expected_runtime)
        self.assertNotIn(weekly_financial_scrape.TESLA_EMAIL_KEY, calls[1][1])
        for _, child_env, _, _ in calls:
            for key in self.UNRELATED_SECRET_ENV:
                self.assertNotIn(key, child_env)

    def test_tesla_identifier_scope_rejects_missing_controlled_or_oversized_values(self):
        for value in (
            None,
            "",
            "owner\n@example.invalid",
            "x" * (weekly_financial_scrape.TESLA_EMAIL_MAX_BYTES + 1),
        ):
            with self.subTest(value_type=type(value).__name__):
                parent = dict(self.credential_parent_environment())
                scoped = weekly_financial_scrape.tesla_source_environment(
                    parent,
                    value,
                )
                child = weekly_financial_scrape.python_child_environment(scoped)
                self.assertNotIn(weekly_financial_scrape.TESLA_EMAIL_KEY, child)
                self.assertEqual(
                    child[weekly_financial_scrape.PYTHON_DOTENV_DISABLED_KEY],
                    "1",
                )

    def test_bwsc_auth_marker_runs_one_guarded_reauth_retry_and_import(self):
        source = self.source_named("bwsc")
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    1,
                    stderr="ERROR: BWSC authentication required",
                ),
                weekly_financial_scrape.CommandResult(0),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("bwsc"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            return next(responses)

        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-bwsc-user",
            "SCRAPER_PW": "private-bwsc-password",
        }
        with (
            patch.object(weekly_financial_scrape, "run_command", side_effect=fake_run),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
            )

        credentials.assert_called_once_with(
            "bwsc",
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (*source.scrape_args, "--run-id", self.RUN_ID),
                source.reauth_args,
                (*source.scrape_args, "--run-id", self.RUN_ID),
                (*source.import_args, "--require-run-id", self.RUN_ID),
            ],
        )
        self.assertTrue(all("--browser-only" not in arguments for arguments, _ in calls))
        for index in (0, 2, 3):
            self.assertNotIn("SCRAPER_USER", calls[index][1])
            self.assertNotIn("SCRAPER_PW", calls[index][1])
        self.assertEqual(calls[1][1]["SCRAPER_USER"], "private-bwsc-user")
        self.assertEqual(calls[1][1]["SCRAPER_PW"], "private-bwsc-password")
        self.assertEqual(
            result,
            {
                "source": "bwsc",
                "scrape": "ok",
                "reauth": "ok",
                "import": "ok",
                "path": "direct_http",
            },
        )

    def test_bwsc_non_auth_safe_failures_never_release_credentials_or_import(self):
        source = self.source_named("bwsc")
        for marker in (
            "ERROR: BWSC request unavailable",
            "ERROR: BWSC API contract validation failed",
        ):
            with self.subTest(marker=marker):
                with (
                    patch.object(
                        weekly_financial_scrape,
                        "run_command",
                        return_value=weekly_financial_scrape.CommandResult(
                            1,
                            stderr=marker,
                        ),
                    ) as run_command,
                    patch.object(
                        weekly_financial_scrape,
                        "credentials_for",
                    ) as credentials,
                ):
                    result = weekly_financial_scrape.run_standard_source(
                        source,
                        self.RUN_ID,
                        self.BASE_ENV,
                        self.CREDENTIAL_STORE,
                    )

                run_command.assert_called_once_with(
                    (*source.scrape_args, "--run-id", self.RUN_ID),
                    self.BASE_ENV,
                )
                credentials.assert_not_called()
                self.assertEqual(result["scrape"], "failed")
                self.assertEqual(result["reauth"], "not_needed")
                self.assertEqual(result["import"], "skipped")
                self.assertEqual(result["path"], "not_observed")

    def test_scraper_status_parser_rejects_unknown_or_ambiguous_markers(self):
        self.assertEqual(
            weekly_financial_scrape.parse_scraper_status(
                self.status_line("bwsc", "browser_recovery"),
                "bwsc",
            ),
            "browser_recovery",
        )
        for output in (
            self.status_line("bwsc", "provider-private-value"),
            self.status_line("bwsc") + "\n" + self.status_line("bwsc", "browser_recovery"),
            "prefix " + self.status_line("bwsc"),
            "FINANCE_SCRAPER_STATUS not-json",
            self.status_line("eversource"),
            self.status_line("bwsc", contract=1),
            self.status_line("bwsc", extra="forbidden"),
            "",
        ):
            with self.subTest(output=output):
                self.assertIsNone(
                    weekly_financial_scrape.parse_scraper_status(output, "bwsc")
                )

    def test_invalid_success_markers_fail_and_never_import(self):
        source = self.source_named("eversource")
        missing_key = (
            "FINANCE_SCRAPER_STATUS "
            '{"contract":2,"source":"eversource"}'
        )
        invalid_outputs = (
            "",
            self.status_line("eversource")
            + "\n"
            + self.status_line("eversource"),
            "FINANCE_SCRAPER_STATUS not-json",
            "FINANCE_SCRAPER_STATUS\t"
            '{"contract":2,"source":"eversource","path":"direct_http"}',
            self.status_line("bwsc"),
            self.status_line("eversource", contract=1),
            self.status_line("eversource", "unrecognized"),
            self.status_line("eversource", extra="forbidden"),
            missing_key,
            (
                "FINANCE_SCRAPER_STATUS "
                '{"contract": 2,"source":"eversource","path":"direct_http"}'
            ),
        )
        for output in invalid_outputs:
            with self.subTest(output=output), patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=weekly_financial_scrape.CommandResult(
                    0,
                    stdout=output,
                ),
            ) as run_command:
                result = weekly_financial_scrape.run_standard_source(
                    source,
                    self.RUN_ID,
                    self.BASE_ENV,
                )

            run_command.assert_called_once_with(
                (*source.scrape_args, "--run-id", self.RUN_ID),
                self.BASE_ENV,
            )
            self.assertEqual(result["scrape"], "failed")
            self.assertEqual(result["path"], "contract_invalid")
            self.assertEqual(result["import"], "skipped")

    def test_valid_browser_fallback_imports_but_marks_path_degraded(self):
        source = self.source_named("bwsc")
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("bwsc", "browser_recovery"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        with patch.object(
            weekly_financial_scrape,
            "run_command",
            side_effect=lambda *args, **kwargs: next(responses),
        ) as run_command:
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
            )

        self.assertEqual(run_command.call_count, 2)
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")
        self.assertEqual(result["path"], "browser_recovery")
        self.assertTrue(weekly_financial_scrape.result_ok(result))
        self.assertTrue(
            weekly_financial_scrape.path_is_degraded(
                result["source"],
                result["path"],
            )
        )

    def test_pennymac_propagates_run_id_and_guards_import(self):
        source = self.source_named("pennymac")
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            if arguments[0] == "scrape_mortgage.py":
                return weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("pennymac"),
                )
            return weekly_financial_scrape.CommandResult(0)

        with patch.object(
            weekly_financial_scrape,
            "run_command",
            side_effect=fake_run,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
            )

        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (*source.scrape_args, "--run-id", self.RUN_ID),
                (*source.import_args, "--require-run-id", self.RUN_ID),
            ],
        )
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")

    def test_failed_pennymac_scrape_never_imports_stale_artifact(self):
        source = self.source_named("pennymac")
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append(arguments)
            return weekly_financial_scrape.CommandResult(
                1,
                stderr="Activity page timed out",
                timed_out=True,
            )

        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=fake_run,
            ),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
            )

        self.assertEqual(
            calls,
            [(*source.scrape_args, "--run-id", self.RUN_ID)],
        )
        self.assertFalse(any("import-json" in argument for call in calls for argument in call))
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "timeout")
        self.assertEqual(result["import"], "skipped")

    def test_boa_success_without_valid_contract_marker_skips_import(self):
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=weekly_financial_scrape.CommandResult(
                    0,
                    stdout="legacy success",
                ),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        run_command.assert_called_once()
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["path"], "contract_invalid")
        self.assertEqual(result["import"], "skipped")

    def test_boa_browser_fallback_imports_with_guarded_run_id(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("boa", "browser_only"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        with patch.object(
            weekly_financial_scrape,
            "run_command",
            side_effect=lambda *args, **kwargs: next(responses),
        ) as run_command:
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        self.assertEqual(run_command.call_count, 2)
        self.assertEqual(
            run_command.call_args_list[1].args[0],
            (
                "update_data.py",
                "import-json-boa-mortgage",
                "--require-run-id",
                self.RUN_ID,
            ),
        )
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")
        self.assertEqual(result["path"], "browser_only")
        self.assertTrue(
            weekly_financial_scrape.path_is_degraded("boa", result["path"])
        )

    def test_boa_reauths_only_for_exact_not_authenticated_status(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="[2026-06-28 08:00:00] boa-tab-verify: not_authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=(
                        "[2026-06-28 08:00:01] "
                        "boa-raw-cdp-reauth: authenticated cookie_total=4"
                    ),
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("boa"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            return next(responses)

        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-user",
            "SCRAPER_PW": "private-password",
        }
        with (
            patch.object(weekly_financial_scrape, "run_command", side_effect=fake_run),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        credentials.assert_called_once_with(
            "boa",
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--wrapper-contract", "2", "--run-id", self.RUN_ID,
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--verify-auth",
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--boa-re-auth",
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--wrapper-contract", "2", "--run-id", self.RUN_ID,
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "update_data.py", "import-json-boa-mortgage",
                    "--require-run-id", self.RUN_ID,
                ),
            ],
        )
        self.assertEqual(calls[2][1]["SCRAPER_USER"], "private-user")
        self.assertNotIn("SCRAPER_USER", calls[3][1])
        self.assertNotIn("SCRAPER_PW", calls[4][1])
        self.assertEqual(result["verify_auth"], "not_authenticated")
        self.assertEqual(result["reauth"], "authenticated")
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")

    def test_boa_reauth_scrubs_selected_credentials_when_command_raises(self):
        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-user",
            "SCRAPER_PW": "private-password",
        }
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=[
                    weekly_financial_scrape.CommandResult(
                        1,
                        stderr="scrape failed",
                    ),
                    weekly_financial_scrape.CommandResult(
                        1,
                        stdout="boa-tab-verify: not_authenticated",
                    ),
                    RuntimeError("private reauth failure"),
                ],
            ),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ),
        ):
            with self.assertRaises(RuntimeError):
                weekly_financial_scrape.run_boa(
                    self.RUN_ID,
                    self.BASE_ENV,
                    self.CREDENTIAL_STORE,
                    instance_id="inst_123abc",
                )

        self.assertNotIn("SCRAPER_USER", credential_env)
        self.assertNotIn("SCRAPER_PW", credential_env)

    def test_boa_missing_tab_is_seeded_reverified_then_guardedly_reauthed(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: boa_tab_unavailable",
                ),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: not_authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="boa-raw-cdp-reauth: authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("boa"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            return next(responses)

        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-user",
            "SCRAPER_PW": "private-password",
        }
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=fake_run,
            ),
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
                return_value="opened",
            ) as ensure_tab,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        ensure_tab.assert_called_once_with("inst_123abc", self.BASE_ENV)
        credentials.assert_called_once_with(
            "boa",
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--wrapper-contract", "2", "--run-id", self.RUN_ID,
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--verify-auth",
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--verify-auth",
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--boa-re-auth",
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--wrapper-contract", "2", "--run-id", self.RUN_ID,
                    "--boa-pinchtab-instance", "inst_123abc",
                ),
                (
                    "update_data.py", "import-json-boa-mortgage",
                    "--require-run-id", self.RUN_ID,
                ),
            ],
        )
        for index in (0, 1, 2, 4, 5):
            self.assertNotIn("SCRAPER_USER", calls[index][1])
            self.assertNotIn("SCRAPER_PW", calls[index][1])
        self.assertEqual(calls[3][1]["SCRAPER_USER"], "private-user")
        self.assertEqual(result["tab_bootstrap"], "opened")
        self.assertEqual(result["verify_auth"], "not_authenticated")
        self.assertEqual(result["reauth"], "authenticated")
        self.assertEqual(result["import"], "ok")

    def test_boa_signed_out_landing_is_seeded_then_requires_exact_reverify(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: signed_out_landing",
                ),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: not_authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="boa-raw-cdp-reauth: authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("boa"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            return next(responses)

        credential_env = {
            **self.BASE_ENV,
            "SCRAPER_USER": "private-user",
            "SCRAPER_PW": "private-password",
        }
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=fake_run,
            ),
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
                return_value="opened",
            ) as ensure_tab,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value=credential_env,
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        ensure_tab.assert_called_once_with("inst_123abc", self.BASE_ENV)
        credentials.assert_called_once_with(
            "boa",
            self.BASE_ENV,
            self.CREDENTIAL_STORE,
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(
            calls[1][0],
            (
                "scrape_mortgage.py",
                "--lender",
                "boa",
                "--verify-auth",
                "--boa-pinchtab-instance",
                "inst_123abc",
            ),
        )
        self.assertEqual(calls[2][0], calls[1][0])
        self.assertNotIn("SCRAPER_USER", calls[2][1])
        self.assertEqual(calls[3][1]["SCRAPER_USER"], "private-user")
        self.assertEqual(result["tab_bootstrap"], "opened")
        self.assertEqual(result["verify_auth"], "not_authenticated")
        self.assertEqual(result["reauth"], "authenticated")
        self.assertEqual(result["import"], "ok")

    def test_boa_seeded_authenticated_tab_retries_without_credentials(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: boa_tab_unavailable",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="boa-tab-verify: authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout=self.status_line("boa"),
                ),
                weekly_financial_scrape.CommandResult(0),
            ]
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=lambda *args, **kwargs: next(responses),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
                return_value="opened",
            ),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        self.assertEqual(run_command.call_count, 5)
        credentials.assert_not_called()
        self.assertEqual(result["tab_bootstrap"], "opened")
        self.assertEqual(result["verify_auth"], "authenticated")
        self.assertEqual(result["scrape"], "ok")
        self.assertEqual(result["import"], "ok")

    def test_boa_tab_bootstrap_failure_never_unlocks_credentials(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: boa_tab_unavailable",
                ),
            ]
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=lambda *args, **kwargs: next(responses),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
                return_value="open_failed",
            ) as ensure_tab,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                instance_id="inst_123abc",
            )

        self.assertEqual(run_command.call_count, 2)
        ensure_tab.assert_called_once_with("inst_123abc", self.BASE_ENV)
        credentials.assert_not_called()
        self.assertEqual(result["tab_bootstrap"], "open_failed")
        self.assertEqual(result["verify_auth"], "boa_tab_unavailable")
        self.assertEqual(result["import"], "skipped")

    def test_boa_post_bootstrap_probe_must_be_exact_before_credentials(self):
        post_bootstrap_outputs = (
            "boa-tab-verify: auth_unknown reason=no_auth_signal",
            "boa-tab-verify: signed_out_landing",
            "boa-tab-verify: not_authenticated_extra",
            "not_authenticated",
            (
                "boa-tab-verify: not_authenticated\n"
                "boa-tab-verify: authenticated"
            ),
        )
        for verify_output in post_bootstrap_outputs:
            with self.subTest(verify_output=verify_output):
                responses = iter(
                    [
                        weekly_financial_scrape.CommandResult(
                            1,
                            stderr="scrape failed",
                        ),
                        weekly_financial_scrape.CommandResult(
                            1,
                            stdout="boa-tab-verify: boa_tab_unavailable",
                        ),
                        weekly_financial_scrape.CommandResult(
                            1,
                            stdout=verify_output,
                        ),
                    ]
                )
                with (
                    patch.object(
                        weekly_financial_scrape,
                        "run_command",
                        side_effect=lambda *args, **kwargs: next(responses),
                    ) as run_command,
                    patch.object(
                        weekly_financial_scrape,
                        "ensure_boa_tab",
                        return_value="opened",
                    ),
                    patch.object(
                        weekly_financial_scrape,
                        "credentials_for",
                    ) as credentials,
                ):
                    result = weekly_financial_scrape.run_boa(
                        self.RUN_ID,
                        self.BASE_ENV,
                        self.CREDENTIAL_STORE,
                        instance_id="inst_123abc",
                    )

                self.assertEqual(run_command.call_count, 3)
                credentials.assert_not_called()
                self.assertEqual(result["tab_bootstrap"], "opened")
                self.assertEqual(result["import"], "skipped")

    def test_boa_preserves_safe_raw_cdp_reauth_failure_status(self):
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: not_authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout=(
                        "[2026-06-28 08:00:01] "
                        "boa-raw-cdp-reauth: cdp_unavailable reason=profile_not_running"
                    ),
                ),
            ]
        )

        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                side_effect=lambda *args, **kwargs: next(responses),
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
                return_value={
                    **self.BASE_ENV,
                    "SCRAPER_USER": "private-user",
                    "SCRAPER_PW": "private-password",
                },
            ),
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                instance_id="inst_123abc",
            )

        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(result["reauth"], "cdp_unavailable")
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["import"], "skipped")

    def test_boa_reauth_status_parser_rejects_unknown_or_ambiguous_output(self):
        self.assertEqual(
            weekly_financial_scrape.parse_boa_reauth_status(
                "boa-raw-cdp-reauth: mfa_or_challenge"
            ),
            "mfa_or_challenge",
        )
        self.assertEqual(
            weekly_financial_scrape.parse_boa_reauth_status(
                "boa-raw-cdp-reauth: host_not_allowed"
            ),
            "host_not_allowed",
        )
        for output in (
            "boa-raw-cdp-reauth: unexpected_status",
            "authenticated",
            (
                "boa-raw-cdp-reauth: authenticated\n"
                "boa-raw-cdp-reauth: cdp_unavailable"
            ),
            (
                "boa-raw-cdp-reauth: authenticated\n"
                "boa-raw-cdp-reauth: unexpected_status"
            ),
        ):
            with self.subTest(output=output):
                self.assertEqual(
                    weekly_financial_scrape.parse_boa_reauth_status(output),
                    "reauth_failed",
                )

    def test_boa_verify_status_parser_allows_only_fixed_safe_statuses(self):
        for status in weekly_financial_scrape.BOA_VERIFY_SAFE_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(
                    weekly_financial_scrape.parse_boa_verify_status(
                        f"[2026-06-28 08:00:00] boa-tab-verify: {status}"
                    ),
                    status,
                )

        for output in (
            "boa-tab-verify: unexpected_status",
            "boa-tab-verify: account_owner",
            "not_authenticated",
            (
                "boa-tab-verify: not_authenticated\n"
                "boa-tab-verify: authenticated"
            ),
        ):
            with self.subTest(output=output):
                self.assertEqual(
                    weekly_financial_scrape.parse_boa_verify_status(output),
                    "verify_failed",
                )

    def test_boa_does_not_reauth_for_other_or_ambiguous_verify_output(self):
        verify_outputs = (
            "[2026-06-28 08:00:00] boa-tab-verify: authenticated",
            "[2026-06-28 08:00:00] boa-tab-verify: cdp_unavailable",
            "[2026-06-28 08:00:00] boa-tab-verify: boa_tab_unavailable_extra",
            "[2026-06-28 08:00:00] boa-tab-verify: signed_out_landing_extra",
            "[2026-06-28 08:00:00] boa-tab-verify: not_authenticated_extra",
            "not_authenticated",
            (
                "[2026-06-28 08:00:00] boa-tab-verify: not_authenticated\n"
                "[2026-06-28 08:00:01] boa-tab-verify: authenticated"
            ),
        )
        for verify_output in verify_outputs:
            with self.subTest(verify_output=verify_output):
                run_command = Mock(
                    side_effect=[
                        weekly_financial_scrape.CommandResult(1, stderr="scrape failed"),
                        weekly_financial_scrape.CommandResult(1, stdout=verify_output),
                    ]
                )
                with (
                    patch.object(
                        weekly_financial_scrape,
                        "run_command",
                        run_command,
                    ),
                    patch.object(
                        weekly_financial_scrape,
                        "credentials_for",
                    ) as credentials,
                    patch.object(
                        weekly_financial_scrape,
                        "ensure_boa_tab",
                    ) as ensure_tab,
                ):
                    result = weekly_financial_scrape.run_boa(
                        self.RUN_ID,
                        self.BASE_ENV,
                        instance_id="inst_123abc",
                    )

                self.assertEqual(run_command.call_count, 2)
                ensure_tab.assert_not_called()
                credentials.assert_not_called()
                self.assertEqual(result["import"], "skipped")

    def test_run_command_captures_child_output_without_echoing_it(self):
        process = Mock(pid=1234, returncode=1)
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ) as popen,
            patch.object(
                weekly_financial_scrape,
                "_read_bounded_child_output",
                return_value=(
                    b"private-child-stdout",
                    b"private-child-stderr",
                ),
            ) as read_output,
            patch.object(
                weekly_financial_scrape,
                "_stop_process_group",
            ) as stop_group,
        ):
            result, output = self.capture_stdout(
                weekly_financial_scrape.run_command,
                ("scraper.py", "--headless"),
                self.BASE_ENV,
            )

        self.assertEqual(output, "")
        self.assertEqual(result.stdout, "private-child-stdout")
        self.assertEqual(result.stderr, "private-child-stderr")
        popen.assert_called_once_with(
            [str(weekly_financial_scrape.PYTHON), "scraper.py", "--headless"],
            cwd=weekly_financial_scrape.REPO,
            env={
                **self.BASE_ENV,
                weekly_financial_scrape.PYTHON_DOTENV_DISABLED_KEY: "1",
            },
            stdout=weekly_financial_scrape.subprocess.PIPE,
            stderr=weekly_financial_scrape.subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        read_output.assert_called_once_with(
            process,
            weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS,
        )
        stop_group.assert_called_once_with(process)
        process.communicate.assert_not_called()

    def test_invalid_utf8_runs_final_process_group_cleanup_before_rejection(self):
        process = Mock(pid=4321, returncode=0)
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ),
            patch.object(
                weekly_financial_scrape,
                "_read_bounded_child_output",
                return_value=(b"\xff", b""),
            ),
            patch.object(
                weekly_financial_scrape,
                "_stop_process_group",
            ) as stop_group,
        ):
            result = weekly_financial_scrape._run_captured(
                [sys.executable, "-c", "pass"],
                self.BASE_ENV,
                1,
            )

        self.assertTrue(result.output_rejected)
        self.assertEqual(
            result.returncode,
            weekly_financial_scrape.CHILD_OUTPUT_REJECTED_RETURN_CODE,
        )
        stop_group.assert_called_once_with(process)

    def test_run_command_kills_entire_process_group_after_timeout(self):
        process = Mock(pid=2468, returncode=None)
        process.wait.return_value = 0
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ),
            patch.object(
                weekly_financial_scrape,
                "_read_bounded_child_output",
                side_effect=weekly_financial_scrape.subprocess.TimeoutExpired(
                    "scraper",
                    1,
                ),
            ),
            patch.object(
                weekly_financial_scrape,
                "_wait_for_process_group_exit",
                side_effect=(False, True),
            ) as wait_for_group,
            patch.object(weekly_financial_scrape.os, "killpg") as killpg,
        ):
            result, output = self.capture_stdout(
                weekly_financial_scrape.run_command,
                ("scraper.py",),
                self.BASE_ENV,
                1,
            )

        self.assertEqual(output, "")
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(
            killpg.call_args_list,
            [
                call(process.pid, weekly_financial_scrape.signal.SIGTERM),
                call(process.pid, weekly_financial_scrape.signal.SIGKILL),
            ],
        )
        self.assertEqual(
            process.wait.call_args_list,
            [
                call(timeout=weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS),
            ],
        )
        self.assertEqual(
            wait_for_group.call_args_list,
            [
                call(
                    process,
                    weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS,
                ),
                call(
                    process,
                    weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS,
                ),
            ],
        )
        process.communicate.assert_not_called()

    def test_run_command_kills_process_group_when_wrapper_is_interrupted(self):
        process = Mock(pid=9753, returncode=None)
        process.wait.return_value = 0
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ),
            patch.object(
                weekly_financial_scrape,
                "_read_bounded_child_output",
                side_effect=KeyboardInterrupt(),
            ),
            patch.object(
                weekly_financial_scrape,
                "_wait_for_process_group_exit",
                return_value=True,
            ),
            patch.object(weekly_financial_scrape.os, "killpg") as killpg,
        ):
            with self.assertRaises(KeyboardInterrupt):
                weekly_financial_scrape.run_command(
                    ("scraper.py",),
                    self.BASE_ENV,
                    1,
                )

        killpg.assert_called_once_with(
            process.pid,
            weekly_financial_scrape.signal.SIGTERM,
        )
        process.wait.assert_called_once_with(
            timeout=weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS
        )
        process.communicate.assert_not_called()

    def test_run_command_cleans_child_when_signal_arrives_during_spawn(self):
        process = Mock(pid=8642, returncode=None)
        process.wait.return_value = 0

        def interrupt_before_process_is_registered(*args, **kwargs):
            self.assertTrue(weekly_financial_scrape._SPAWNING_PROCESS)
            self.assertIsNone(weekly_financial_scrape._ACTIVE_PROCESS)
            weekly_financial_scrape._termination_handler(
                weekly_financial_scrape.signal.SIGTERM,
                None,
            )
            return process

        with (
            patch.object(
                weekly_financial_scrape,
                "_ACTIVE_PROCESS",
                None,
            ),
            patch.object(
                weekly_financial_scrape,
                "_SPAWNING_PROCESS",
                False,
            ),
            patch.object(
                weekly_financial_scrape,
                "_DEFERRED_TERMINATION_SIGNAL",
                None,
            ),
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                side_effect=interrupt_before_process_is_registered,
            ),
            patch.object(
                weekly_financial_scrape,
                "_wait_for_process_group_exit",
                return_value=True,
            ),
            patch.object(weekly_financial_scrape.os, "killpg") as killpg,
        ):
            with self.assertRaises(
                weekly_financial_scrape.WrapperInterrupted
            ) as raised:
                weekly_financial_scrape.run_command(
                    ("scraper.py",),
                    self.BASE_ENV,
                    1,
                )

            self.assertEqual(
                raised.exception.signum,
                weekly_financial_scrape.signal.SIGTERM,
            )
            self.assertFalse(weekly_financial_scrape._SPAWNING_PROCESS)
            self.assertIsNone(weekly_financial_scrape._ACTIVE_PROCESS)
            self.assertIsNone(
                weekly_financial_scrape._DEFERRED_TERMINATION_SIGNAL
            )

        killpg.assert_called_once_with(
            process.pid,
            weekly_financial_scrape.signal.SIGTERM,
        )
        process.wait.assert_called_once_with(
            timeout=weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS
        )
        process.communicate.assert_not_called()

    def test_stop_process_group_kills_term_ignoring_descendant(self):
        descendant_script = (
            "import signal,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); "
            "time.sleep(60)"
        )
        leader_script = (
            "import signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0)); "
            "child=subprocess.Popen([sys.executable,'-c',"
            f"{descendant_script!r}], stdout=subprocess.PIPE); "
            "child.stdout.readline(); "
            "print(child.pid, flush=True); "
            "time.sleep(60)"
        )
        process = weekly_financial_scrape.subprocess.Popen(
            [sys.executable, "-c", leader_script],
            stdout=weekly_financial_scrape.subprocess.PIPE,
            stderr=weekly_financial_scrape.subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        descendant_pid = None
        try:
            descendant_pid = int(
                process.stdout.readline(32).decode("ascii").strip()
            )
            with (
                patch.object(
                    weekly_financial_scrape,
                    "PROCESS_GROUP_GRACE_SECONDS",
                    0.2,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "PROCESS_GROUP_POLL_SECONDS",
                    0.01,
                ),
            ):
                weekly_financial_scrape._stop_process_group(process)

            for _attempt in range(100):
                if not weekly_financial_scrape._process_group_exists(process):
                    break
                weekly_financial_scrape.time.sleep(0.01)

            self.assertIsNotNone(process.returncode)
            self.assertFalse(
                weekly_financial_scrape._process_group_exists(process)
            )
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
        finally:
            try:
                os.killpg(process.pid, weekly_financial_scrape.signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except weekly_financial_scrape.subprocess.TimeoutExpired:
                pass

    def test_normal_capture_removes_pipe_closing_descendant_before_return(self):
        descendant_script = (
            "import os,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "os.close(1); os.close(2); "
            "time.sleep(60)"
        )
        leader_script = (
            "import subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-c',"
            f"{descendant_script!r}]); "
            "print(child.pid, flush=True)"
        )
        descendant_pid = None
        leader_pid = None
        real_popen = weekly_financial_scrape.subprocess.Popen

        def recording_popen(*args, **kwargs):
            nonlocal leader_pid
            process = real_popen(*args, **kwargs)
            leader_pid = process.pid
            return process

        try:
            with (
                patch.object(
                    weekly_financial_scrape,
                    "PROCESS_GROUP_GRACE_SECONDS",
                    0.2,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "PROCESS_GROUP_POLL_SECONDS",
                    0.01,
                ),
                patch.object(
                    weekly_financial_scrape.subprocess,
                    "Popen",
                    side_effect=recording_popen,
                ),
            ):
                result = weekly_financial_scrape._run_captured(
                    [sys.executable, "-c", leader_script],
                    self.BASE_ENV,
                    5,
                )

            self.assertEqual(result.returncode, 0)
            descendant_pid = int(result.stdout.strip())
            for _attempt in range(100):
                try:
                    os.kill(descendant_pid, 0)
                except ProcessLookupError:
                    break
                weekly_financial_scrape.time.sleep(0.01)
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
        finally:
            if descendant_pid is not None:
                try:
                    os.kill(descendant_pid, weekly_financial_scrape.signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if leader_pid is not None:
                try:
                    os.killpg(leader_pid, weekly_financial_scrape.signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_child_output_at_exact_aggregate_limit_is_accepted(self):
        limit = weekly_financial_scrape.CHILD_OUTPUT_MAX_BYTES
        result = weekly_financial_scrape._run_captured(
            [
                sys.executable,
                "-c",
                f"import os; os.write(1, b'x' * {limit})",
            ],
            self.BASE_ENV,
            5,
        )

        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.output_rejected)
        self.assertEqual(len(result.stdout.encode("utf-8")), limit)
        self.assertEqual(result.stderr, "")

    def test_oversized_combined_output_is_discarded_and_fails_closed(self):
        marker = weekly_financial_scrape.AUTH_FAILURE_LINES["eversource"]
        limit = weekly_financial_scrape.CHILD_OUTPUT_MAX_BYTES
        script = (
            "import os; "
            f"os.write(2, {marker.encode('utf-8')!r} + b'\\n'); "
            f"os.write(1, b'x' * {limit})"
        )
        result = weekly_financial_scrape._run_captured(
            [sys.executable, "-c", script],
            self.BASE_ENV,
            5,
        )

        self.assertEqual(
            result.returncode,
            weekly_financial_scrape.CHILD_OUTPUT_REJECTED_RETURN_CODE,
        )
        self.assertTrue(result.output_rejected)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(
            weekly_financial_scrape.is_auth_failure(result, "eversource")
        )

    def test_invalid_utf8_on_either_stream_is_discarded_and_fails_closed(self):
        for descriptor in (1, 2):
            with self.subTest(descriptor=descriptor):
                result = weekly_financial_scrape._run_captured(
                    [
                        sys.executable,
                        "-c",
                        f"import os; os.write({descriptor}, b'valid\\n\\xff')",
                    ],
                    self.BASE_ENV,
                    5,
                )

                self.assertEqual(
                    result.returncode,
                    weekly_financial_scrape.CHILD_OUTPUT_REJECTED_RETURN_CODE,
                )
                self.assertTrue(result.output_rejected)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_rejected_standard_output_never_reauths_or_imports(self):
        source = self.source_named("eversource")
        rejected = weekly_financial_scrape.CommandResult(
            weekly_financial_scrape.CHILD_OUTPUT_REJECTED_RETURN_CODE,
            stderr=weekly_financial_scrape.AUTH_FAILURE_LINES["eversource"],
            output_rejected=True,
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=rejected,
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
        ):
            result = weekly_financial_scrape.run_standard_source(
                source,
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
            )

        self.assertEqual(run_command.call_count, 1)
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_rejected_boa_scrape_never_verifies_reauths_or_imports(self):
        rejected = weekly_financial_scrape.CommandResult(
            weekly_financial_scrape.CHILD_OUTPUT_REJECTED_RETURN_CODE,
            stdout="boa-tab-verify: not_authenticated",
            output_rejected=True,
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                return_value=rejected,
            ) as run_command,
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
            ) as ensure_tab,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                "inst_123abc",
            )

        self.assertEqual(run_command.call_count, 1)
        credentials.assert_not_called()
        ensure_tab.assert_not_called()
        self.assertEqual(result["verify_auth"], "not_needed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_rejected_boa_verify_marker_never_triggers_reauth_or_import(self):
        run_command = Mock(
            side_effect=[
                weekly_financial_scrape.CommandResult(1),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="boa-tab-verify: not_authenticated",
                    output_rejected=True,
                ),
            ]
        )
        with (
            patch.object(
                weekly_financial_scrape,
                "run_command",
                run_command,
            ),
            patch.object(
                weekly_financial_scrape,
                "credentials_for",
            ) as credentials,
            patch.object(
                weekly_financial_scrape,
                "ensure_boa_tab",
            ) as ensure_tab,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                "inst_123abc",
            )

        self.assertEqual(run_command.call_count, 2)
        credentials.assert_not_called()
        ensure_tab.assert_not_called()
        self.assertEqual(result["verify_auth"], "verify_failed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_rejected_boa_reauth_marker_never_retries_or_imports(self):
        run_command = Mock(
            side_effect=[
                weekly_financial_scrape.CommandResult(1),
                weekly_financial_scrape.CommandResult(
                    1,
                    stdout="boa-tab-verify: not_authenticated",
                ),
                weekly_financial_scrape.CommandResult(
                    0,
                    stdout="boa-raw-cdp-reauth: authenticated",
                    output_rejected=True,
                ),
            ]
        )
        with patch.object(
            weekly_financial_scrape,
            "run_command",
            run_command,
        ):
            result = weekly_financial_scrape.run_boa(
                self.RUN_ID,
                self.BASE_ENV,
                self.CREDENTIAL_STORE,
                "inst_123abc",
            )

        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(result["verify_auth"], "not_authenticated")
        self.assertEqual(result["reauth"], "reauth_failed")
        self.assertEqual(result["import"], "skipped")

    def test_final_status_is_atomically_written_owner_only(self):
        payload = {
            "contract": 2,
            "status": "degraded",
            "run_id": self.RUN_ID,
            "results": [
                {
                    "source": "bwsc",
                    "scrape": "ok",
                    "reauth": "not_needed",
                    "import": "ok",
                    "path": "browser_recovery",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            with patch.object(
                weekly_financial_scrape.os,
                "replace",
                wraps=os.replace,
            ) as replace:
                weekly_financial_scrape.write_final_status(payload, status_path)

            self.assertEqual(
                json.loads(status_path.read_text(encoding="utf-8")),
                payload,
            )
            self.assertEqual(status_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(status_path.parent.stat().st_mode & 0o777, 0o700)
            replace.assert_called_once()
            temporary_path, destination = replace.call_args.args
            self.assertEqual(Path(temporary_path).parent, status_path.parent)
            self.assertEqual(Path(destination), status_path)
            self.assertFalse(Path(temporary_path).exists())

    def test_final_status_rejects_symlink_and_insecure_parent(self):
        payload = {"contract": 2, "status": "ok", "run_id": self.RUN_ID}
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            state = root / "state"
            state.mkdir(mode=0o700)
            target = root / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            target.chmod(0o600)
            link = state / "weekly.json"
            link.symlink_to(target)

            with self.assertRaises(weekly_financial_scrape.FinalStatusError):
                weekly_financial_scrape.write_final_status(payload, link)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")

            link.unlink()
            state.chmod(0o755)
            with self.assertRaises(weekly_financial_scrape.FinalStatusError):
                weekly_financial_scrape.write_final_status(payload, link)
            self.assertFalse(link.exists())

    def test_degraded_run_persists_status_and_returns_nonzero(self):
        degraded_result = {
            "source": "bwsc",
            "scrape": "ok",
            "reauth": "not_needed",
            "import": "ok",
            "path": "browser_only",
        }
        boa_result = {
            "source": "boa",
            "scrape": "ok",
            "verify_auth": "not_needed",
            "tab_bootstrap": "not_needed",
            "reauth": "not_needed",
            "import": "ok",
            "path": "direct_http",
        }
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"
            python = repo / "venv" / "bin" / "python3"
            status_path = root / "state" / "weekly.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "SOURCES",
                    (self.source_named("bwsc"),),
                ),
                patch.object(
                    weekly_financial_scrape,
                    "scraper_contract_preflight",
                    return_value={"status": "contract_ok", "contract": 2},
                ),
                patch.object(
                    weekly_financial_scrape,
                    "credential_preflight",
                    return_value=(
                        self.BASE_ENV,
                        self.CREDENTIAL_STORE,
                        {"status": "preflight_ok"},
                    ),
                ),
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_profile",
                    return_value=weekly_financial_scrape.BoaProfileResult(
                        "ok",
                        "inst_123abc",
                    ),
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_standard_source",
                    return_value=degraded_result,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_boa",
                    return_value=dict(boa_result),
                ),
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

            self.assertEqual(returncode, 1)
            payloads = [json.loads(line) for line in output.splitlines()]
            final = payloads[-1]
            self.assertEqual(final["status"], "degraded")
            self.assertEqual(final["run_id"], self.RUN_ID)
            self.assertEqual(final["results"][0]["path"], "browser_only")
            self.assertEqual(
                final,
                json.loads(status_path.read_text(encoding="utf-8")),
            )

    def test_interrupted_run_persists_safe_final_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"
            python = repo / "venv" / "bin" / "python3"
            status_path = root / "state" / "weekly.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "scraper_contract_preflight",
                    side_effect=weekly_financial_scrape.WrapperInterrupted(
                        weekly_financial_scrape.signal.SIGTERM
                    ),
                ),
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

            self.assertEqual(
                returncode,
                128 + weekly_financial_scrape.signal.SIGTERM,
            )
            final = json.loads(output)
            self.assertEqual(final["status"], "interrupted")
            self.assertEqual(final["signal"], "sigterm")
            self.assertEqual(final["run_id"], self.RUN_ID)
            self.assertEqual(
                final,
                json.loads(status_path.read_text(encoding="utf-8")),
            )

    def test_unexpected_run_error_persists_safe_internal_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = root / "repo"
            python = repo / "venv" / "bin" / "python3"
            status_path = root / "state" / "weekly.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            with (
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "scraper_contract_preflight",
                    side_effect=RuntimeError("private failure detail"),
                ),
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape._run_locked
                )

            self.assertEqual(returncode, 1)
            self.assertNotIn("private failure detail", output)
            final = json.loads(output)
            self.assertEqual(final["status"], "internal_error")
            self.assertEqual(final["run_id"], self.RUN_ID)
            self.assertEqual(
                final,
                json.loads(status_path.read_text(encoding="utf-8")),
            )

    def test_finish_run_enqueues_one_private_redacted_alert_for_failure(self):
        failed_result = {
            "source": "eversource",
            "scrape": "failed",
            "reauth": "not_needed",
            "import": "skipped",
            "path": "not_observed",
            "private": "must-not-persist",
        }
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            with patch.object(
                weekly_financial_scrape,
                "FINAL_STATUS_PATH",
                status_path,
            ):
                result, output = self.capture_stdout(
                    weekly_financial_scrape.finish_run,
                    "failed",
                    self.RUN_ID,
                    results=[failed_result],
                )

            self.assertTrue(result)
            final = json.loads(output)
            self.assertEqual(final["status"], "failed")
            self.assertEqual(final["alert_handoff"], "persisted")
            self.assertEqual(
                json.loads(status_path.read_text(encoding="utf-8"))["alert_handoff"],
                "persisted",
            )
            outbox = status_path.parent / weekly_financial_scrape.ALERT_OUTBOX_NAME
            alert_path = outbox / f"{self.RUN_ID}.json"
            alert = json.loads(alert_path.read_text(encoding="utf-8"))
            self.assertTrue(
                weekly_financial_scrape.validate_alert_payload(
                    alert,
                    expected_run_id=self.RUN_ID,
                )
            )
            self.assertEqual(alert["status"], "failed")
            self.assertEqual(alert["contract"], 2)
            self.assertEqual(alert["delivery_state"], "pending")
            self.assertIsNone(alert["sent_at"])
            self.assertEqual(alert["affected"][0]["source"], "eversource")
            self.assertNotIn("private", alert["affected"][0]["states"])
            self.assertNotIn("must-not-persist", alert_path.read_text(encoding="utf-8"))
            self.assertEqual(outbox.stat().st_mode & 0o777, 0o700)
            self.assertEqual(alert_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(alert_path.stat().st_nlink, 1)

    def test_healthy_finish_never_creates_alert_outbox(self):
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            with patch.object(
                weekly_financial_scrape,
                "FINAL_STATUS_PATH",
                status_path,
            ):
                result, output = self.capture_stdout(
                    weekly_financial_scrape.finish_run,
                    "ok",
                    self.RUN_ID,
                    results=[],
                )

            self.assertTrue(result)
            self.assertEqual(json.loads(output)["alert_handoff"], "not_required")
            self.assertFalse(
                (status_path.parent / weekly_financial_scrape.ALERT_OUTBOX_NAME).exists()
            )

    def test_alert_enqueue_is_idempotent_and_preserves_retry_state(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = Path(tempdir) / "state" / "outbox"
            outbox.parent.mkdir(mode=0o700)
            created_at = "2026-07-19T12:00:00+00:00"
            alert = weekly_financial_scrape.build_alert_payload(
                "internal_error",
                self.RUN_ID,
                created_at,
            )
            self.assertTrue(weekly_financial_scrape.enqueue_alert(alert, outbox))
            path = outbox / f"{self.RUN_ID}.json"
            retained = json.loads(path.read_text(encoding="utf-8"))
            retained["attempts"] = 1
            retained["last_attempt_at"] = created_at
            retained["last_error"] = "send_failed"
            path.write_text(json.dumps(retained), encoding="utf-8")
            path.chmod(0o600)

            self.assertFalse(weekly_financial_scrape.enqueue_alert(alert, outbox))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["attempts"],
                1,
            )

    def test_status_write_failure_is_itself_durably_alerted(self):
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            status_path.parent.mkdir(mode=0o700)
            with (
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "write_final_status",
                    side_effect=weekly_financial_scrape.FinalStatusError,
                ),
            ):
                result, output = self.capture_stdout(
                    weekly_financial_scrape.finish_run,
                    "ok",
                    self.RUN_ID,
                    results=[],
                )

            self.assertFalse(result)
            final = json.loads(output)
            self.assertEqual(final["status"], "status_write_failed")
            self.assertTrue(final["alert_persisted"])
            alert_path = (
                status_path.parent
                / weekly_financial_scrape.ALERT_OUTBOX_NAME
                / f"{self.RUN_ID}.json"
            )
            alert = json.loads(alert_path.read_text(encoding="utf-8"))
            self.assertEqual(alert["status"], "status_write_failed")
            self.assertEqual(alert["run_status"], "ok")

    def test_alert_enqueue_failure_makes_persistence_unhealthy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            with (
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "enqueue_alert",
                    side_effect=weekly_financial_scrape.AlertOutboxError,
                ),
            ):
                result, output = self.capture_stdout(
                    weekly_financial_scrape.finish_run,
                    "failed",
                    self.RUN_ID,
                )

            self.assertFalse(result)
            final = json.loads(output)
            self.assertEqual(final["status"], "alert_enqueue_failed")
            self.assertTrue(final["status_persisted"])
            self.assertTrue(final["alert_handoff_persisted"])
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["alert_handoff"], "failed")

    def test_alert_handoff_pending_marker_survives_final_status_rewrite_failure(self):
        with tempfile.TemporaryDirectory() as tempdir:
            status_path = Path(tempdir) / "state" / "weekly.json"
            real_write = weekly_financial_scrape.write_final_status
            writes = 0

            def fail_second_write(payload, path=None):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise weekly_financial_scrape.FinalStatusError
                return real_write(payload, path)

            with (
                patch.object(weekly_financial_scrape, "FINAL_STATUS_PATH", status_path),
                patch.object(
                    weekly_financial_scrape,
                    "write_final_status",
                    side_effect=fail_second_write,
                ),
            ):
                result, output = self.capture_stdout(
                    weekly_financial_scrape.finish_run,
                    "failed",
                    self.RUN_ID,
                )

            self.assertFalse(result)
            self.assertEqual(json.loads(output)["status"], "status_write_failed")
            persisted = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["alert_handoff"], "pending")
            self.assertTrue(
                (status_path.parent / weekly_financial_scrape.ALERT_OUTBOX_NAME / f"{self.RUN_ID}.json").exists()
            )

    def test_singleton_lock_is_nonblocking_and_released_after_unwind(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = Path(tempdir) / "state" / ".weekly-scrape.lock"
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                lock_path,
            ):
                with weekly_financial_scrape.singleton_lock() as acquired:
                    self.assertTrue(acquired)
                    with weekly_financial_scrape.singleton_lock() as second:
                        self.assertFalse(second)

                with self.assertRaises(RuntimeError):
                    with weekly_financial_scrape.singleton_lock() as acquired:
                        self.assertTrue(acquired)
                        raise RuntimeError("fixture interruption")

                with weekly_financial_scrape.singleton_lock() as acquired_again:
                    self.assertTrue(acquired_again)

            self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)

    def test_singleton_lock_rejects_symlinked_or_insecure_paths(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o700)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                linked_parent / "lock",
            ):
                with self.assertRaises(weekly_financial_scrape.RunLockError):
                    with weekly_financial_scrape.singleton_lock():
                        self.fail("symlinked parent must not be entered")
            self.assertFalse((real_parent / "lock").exists())

            private_parent = root / "private-parent"
            private_parent.mkdir(mode=0o700)
            target = root / "target"
            target.write_text("unchanged", encoding="utf-8")
            target.chmod(0o600)
            linked_lock = private_parent / "lock"
            linked_lock.symlink_to(target)
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                linked_lock,
            ):
                with self.assertRaises(weekly_financial_scrape.RunLockError):
                    with weekly_financial_scrape.singleton_lock():
                        self.fail("symlinked lock must not be entered")
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

            insecure_parent = root / "insecure-parent"
            insecure_parent.mkdir(mode=0o755)
            insecure_parent.chmod(0o755)
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                insecure_parent / "lock",
            ):
                with self.assertRaises(weekly_financial_scrape.RunLockError):
                    with weekly_financial_scrape.singleton_lock():
                        self.fail("insecure parent must not be entered")
            self.assertEqual(insecure_parent.stat().st_mode & 0o777, 0o755)

    def test_singleton_lock_rejects_insecure_or_hardlinked_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            parent = Path(tempdir) / "state"
            parent.mkdir(mode=0o700)

            insecure = parent / "insecure.lock"
            insecure.touch(mode=0o600)
            insecure.chmod(0o644)
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                insecure,
            ):
                with self.assertRaises(weekly_financial_scrape.RunLockError):
                    with weekly_financial_scrape.singleton_lock():
                        self.fail("insecure lock must not be entered")
            self.assertEqual(insecure.stat().st_mode & 0o777, 0o644)

            original = parent / "original.lock"
            original.touch(mode=0o600)
            hardlink = parent / "hardlinked.lock"
            os.link(original, hardlink)
            with patch.object(
                weekly_financial_scrape,
                "LOCK_PATH",
                hardlink,
            ):
                with self.assertRaises(weekly_financial_scrape.RunLockError):
                    with weekly_financial_scrape.singleton_lock():
                        self.fail("hardlinked lock must not be entered")
            self.assertEqual(original.stat().st_nlink, 2)

    def test_main_reports_lock_contention_as_unhealthy_and_durably_alerts(self):
        @weekly_financial_scrape.contextmanager
        def unavailable_lock():
            yield False

        with tempfile.TemporaryDirectory() as tempdir:
            state_dir = Path(tempdir) / "state"
            state_dir.mkdir(mode=0o700)
            status_path = state_dir / "weekly-scrape-status.json"
            with (
                patch.object(sys, "argv", [str(MODULE_PATH)]),
                patch.object(
                    weekly_financial_scrape,
                    "singleton_lock",
                    unavailable_lock,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    status_path,
                ),
                patch.object(
                    weekly_financial_scrape.uuid,
                    "uuid4",
                    return_value=self.RUN_ID,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "write_final_status",
                ) as write_final_status,
                patch.object(
                    weekly_financial_scrape,
                    "_run_locked",
                ) as run_locked,
            ):
                returncode, output = self.capture_stdout(
                    weekly_financial_scrape.main
                )

            self.assertEqual(returncode, 1)
            self.assertEqual(
                json.loads(output),
                {
                    "alert_handoff": "persisted",
                    "contract": weekly_financial_scrape.SCRAPER_CONTRACT_VERSION,
                    "reason": "already_running",
                    "run_id": self.RUN_ID,
                    "status": "lock_unavailable",
                },
            )
            self.assertLessEqual(len(output.encode("utf-8")), 512)
            run_locked.assert_not_called()
            write_final_status.assert_not_called()
            self.assertFalse(status_path.exists())
            alert_path = (
                state_dir
                / weekly_financial_scrape.ALERT_OUTBOX_NAME
                / f"{self.RUN_ID}.json"
            )
            alert = json.loads(alert_path.read_text(encoding="utf-8"))
            self.assertTrue(
                weekly_financial_scrape.validate_alert_payload(
                    alert,
                    expected_run_id=self.RUN_ID,
                )
            )
            self.assertEqual(alert["status"], "lock_unavailable")

    def test_main_lock_contention_stays_nonzero_when_alert_handoff_fails(self):
        @weekly_financial_scrape.contextmanager
        def unavailable_lock():
            yield False

        with (
            patch.object(sys, "argv", [str(MODULE_PATH)]),
            patch.object(
                weekly_financial_scrape,
                "singleton_lock",
                unavailable_lock,
            ),
            patch.object(
                weekly_financial_scrape,
                "enqueue_alert",
                side_effect=weekly_financial_scrape.AlertOutboxError,
            ),
            patch.object(
                weekly_financial_scrape.uuid,
                "uuid4",
                return_value=self.RUN_ID,
            ),
            patch.object(weekly_financial_scrape, "_run_locked") as run_locked,
        ):
            returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 1)
        self.assertEqual(json.loads(output)["alert_handoff"], "failed")
        self.assertEqual(json.loads(output)["status"], "lock_unavailable")
        run_locked.assert_not_called()

    def test_main_never_echoes_captured_child_output(self):
        source = weekly_financial_scrape.Source(
            "eversource",
            ("fixture_scraper.py",),
            ("update_data.py", "import-json-fixture"),
        )
        private_markers = ("private-child-stdout", "private-child-stderr")
        calls = []
        events = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
            if arguments == weekly_financial_scrape.SCRAPER_CONTRACT_COMMAND:
                events.append("contract_version_preflight")
                return self.contract_result()
            if arguments == weekly_financial_scrape.SCRAPER_MANIFEST_COMMAND:
                events.append("contract_manifest_preflight")
                return self.manifest_result()
            events.append("source")
            if arguments[0] == "fixture_scraper.py":
                return weekly_financial_scrape.CommandResult(
                    0,
                    stdout=(
                        self.status_line("eversource")
                        + "\n"
                        + private_markers[0]
                    ),
                    stderr=private_markers[1],
                )
            return weekly_financial_scrape.CommandResult(
                0,
                stdout=private_markers[0],
                stderr=private_markers[1],
            )

        boa_result = {
            "source": "boa",
            "scrape": "ok",
            "verify_auth": "not_needed",
            "reauth": "not_needed",
            "import": "ok",
            "path": "direct_http",
        }
        boa_calls = []
        profile_preflight_calls = []

        def fake_boa(
            run_id,
            env,
            credential_store=None,
            instance_id=None,
            provider_modes=None,
        ):
            events.append("boa")
            boa_calls.append(
                (
                    run_id,
                    dict(env),
                    dict(credential_store or {}),
                    instance_id,
                )
            )
            return boa_result

        def fake_profile_preflight(env):
            events.append("profile_preflight")
            profile_preflight_calls.append(dict(env))
            return weekly_financial_scrape.BoaProfileResult(
                "ok",
                "inst_123abc",
            )

        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            repo = temp_path / "repo"
            python = repo / "venv" / "bin" / "python3"
            cache = temp_path / "scraper-credentials.json"
            python.parent.mkdir(parents=True)
            python.touch()
            self.write_repo_dotenv(repo)
            self.write_credential_cache(cache)

            with (
                patch.object(sys, "argv", [str(MODULE_PATH)]),
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(
                    weekly_financial_scrape,
                    "FINANCE_CREDENTIAL_CACHE",
                    cache,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "LOCK_PATH",
                    temp_path / "state" / ".weekly-scrape.lock",
                ),
                patch.object(
                    weekly_financial_scrape,
                    "FINAL_STATUS_PATH",
                    temp_path / "state" / "weekly-scrape-status.json",
                ),
                patch.object(weekly_financial_scrape, "SOURCES", (source,)),
                patch.object(weekly_financial_scrape, "run_command", side_effect=fake_run),
                patch.object(
                    weekly_financial_scrape,
                    "ensure_boa_profile",
                    side_effect=fake_profile_preflight,
                ),
                patch.object(
                    weekly_financial_scrape,
                    "run_boa",
                    side_effect=fake_boa,
                ),
                patch.object(weekly_financial_scrape.uuid, "uuid4", return_value=self.RUN_ID),
                patch.dict(
                    os.environ,
                    self.credential_parent_environment(),
                    clear=True,
                ),
            ):
                returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 0)
        for marker in private_markers:
            self.assertNotIn(marker, output)
        self.assertEqual(
            events,
            [
                "contract_version_preflight",
                "contract_manifest_preflight",
                "profile_preflight",
                "source",
                "source",
                "boa",
            ],
        )
        self.assertEqual(len(calls), 4)
        for _, child_env in calls:
            self.assertEqual(child_env, self.BASE_ENV)
            self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", child_env)
            self.assertNotIn("SCRAPER_USER", child_env)
            self.assertNotIn("SCRAPER_PW", child_env)
            for key in weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS:
                self.assertNotIn(key, child_env)
        self.assertEqual(len(boa_calls), 1)
        self.assertEqual(len(profile_preflight_calls), 1)
        self.assertEqual(profile_preflight_calls[0], self.BASE_ENV)
        self.assertNotIn(
            "OP_SERVICE_ACCOUNT_TOKEN",
            profile_preflight_calls[0],
        )
        self.assertNotIn("SCRAPER_USER", profile_preflight_calls[0])
        self.assertNotIn("SCRAPER_PW", profile_preflight_calls[0])
        for key in weekly_financial_scrape.FINANCE_CREDENTIAL_ENV_KEYS:
            self.assertNotIn(key, profile_preflight_calls[0])
        boa_child_env = boa_calls[0][1]
        boa_credential_store = boa_calls[0][2]
        self.assertEqual(boa_calls[0][3], "inst_123abc")
        self.assertEqual(boa_child_env, self.BASE_ENV)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", boa_child_env)
        self.assertNotIn("SCRAPER_USER", boa_child_env)
        self.assertNotIn("SCRAPER_PW", boa_child_env)
        self.assertEqual(boa_credential_store, self.CREDENTIAL_STORE)
        payloads = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(payloads[0]["source"], "eversource")
        self.assertEqual(payloads[1]["source"], "boa")
        self.assertEqual(payloads[1]["profile_preflight"], "ok")
        self.assertEqual(payloads[2]["status"], "ok")
        self.assertEqual(payloads[2]["run_id"], self.RUN_ID)


if __name__ == "__main__":
    unittest.main()
