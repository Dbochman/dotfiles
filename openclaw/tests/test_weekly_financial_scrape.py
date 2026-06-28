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
    BASE_ENV = {"PATH": "/usr/bin:/bin", "SAFE_PARENT_VALUE": "present"}

    @staticmethod
    def source_named(name):
        return next(
            source for source in weekly_financial_scrape.SOURCES
            if source.name == name
        )

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
        self.assertEqual(run_command.call_args.args[0], source.scrape_args)
        credentials.assert_not_called()
        self.assertEqual(result["scrape"], "failed")
        self.assertEqual(result["reauth"], "not_needed")
        self.assertEqual(result["import"], "skipped")

    def test_authentication_required_is_recognized_without_matching_shell_failures(self):
        self.assertTrue(
            weekly_financial_scrape.is_auth_failure(
                weekly_financial_scrape.CommandResult(
                    1,
                    stderr="PennyMac API failed: authentication required",
                )
            )
        )
        for stderr in (
            "zsh: command not found: scraper",
            "service-account token unavailable",
            "authentication service unavailable",
        ):
            with self.subTest(stderr=stderr):
                self.assertFalse(
                    weekly_financial_scrape.is_auth_failure(
                        weekly_financial_scrape.CommandResult(1, stderr=stderr)
                    )
                )

    def test_credentials_use_op_environment_but_return_token_free_child_environment(self):
        op_env = {
            **self.BASE_ENV,
            "OP_SERVICE_ACCOUNT_TOKEN": "private-service-token",
        }
        with patch.object(
            weekly_financial_scrape,
            "run_op_read",
            side_effect=["private-user", "private-password"],
        ) as op_read:
            credential_env = weekly_financial_scrape.credentials_for(
                "Fixture Item",
                self.BASE_ENV,
                op_env,
            )

        self.assertEqual(
            op_read.call_args_list,
            [
                call("Fixture Item", "username", op_env),
                call("Fixture Item", "password", op_env),
            ],
        )
        self.assertEqual(credential_env["SCRAPER_USER"], "private-user")
        self.assertEqual(credential_env["SCRAPER_PW"], "private-password")
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", credential_env)

    def test_standard_source_reauths_once_for_recognized_auth_failure(self):
        source = self.source_named("eversource")
        op_env = {
            **self.BASE_ENV,
            "OP_SERVICE_ACCOUNT_TOKEN": "private-service-token",
        }
        responses = iter(
            [
                weekly_financial_scrape.CommandResult(
                    1,
                    stderr="Session expired and running in headless mode",
                ),
                weekly_financial_scrape.CommandResult(0),
                weekly_financial_scrape.CommandResult(0),
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
                op_env,
            )

        credentials.assert_called_once_with(source.op_item, self.BASE_ENV, op_env)
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                source.scrape_args,
                source.reauth_args,
                source.scrape_args,
                source.import_args,
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

    def test_pennymac_propagates_run_id_and_guards_import(self):
        source = self.source_named("pennymac")
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
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

    def test_boa_reauths_only_for_exact_not_authenticated_status(self):
        op_env = {
            **self.BASE_ENV,
            "OP_SERVICE_ACCOUNT_TOKEN": "private-service-token",
        }
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
                weekly_financial_scrape.CommandResult(0),
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
                op_env,
            )

        credentials.assert_called_once_with("Bank of America", self.BASE_ENV, op_env)
        self.assertEqual(
            [arguments for arguments, _ in calls],
            [
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--run-id", self.RUN_ID,
                ),
                ("scrape_mortgage.py", "--lender", "boa", "--verify-auth"),
                ("scrape_mortgage.py", "--lender", "boa", "--boa-re-auth"),
                (
                    "scrape_mortgage.py", "--lender", "boa", "--headless",
                    "--merge", "--run-id", self.RUN_ID,
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
                ):
                    result = weekly_financial_scrape.run_boa(
                        self.RUN_ID,
                        self.BASE_ENV,
                    )

                self.assertEqual(run_command.call_count, 2)
                credentials.assert_not_called()
                self.assertEqual(result["import"], "skipped")

    def test_run_command_captures_child_output_without_echoing_it(self):
        process = Mock(pid=1234, returncode=1)
        process.communicate.return_value = (
            "private-child-stdout",
            "private-child-stderr",
        )
        with patch.object(
            weekly_financial_scrape.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
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
            env=self.BASE_ENV,
            stdout=weekly_financial_scrape.subprocess.PIPE,
            stderr=weekly_financial_scrape.subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        process.communicate.assert_called_once_with(
            timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS
        )

    def test_run_command_kills_entire_process_group_after_timeout(self):
        process = Mock(pid=2468, returncode=None)
        process.poll.return_value = None
        process.communicate.side_effect = [
            weekly_financial_scrape.subprocess.TimeoutExpired("scraper", 1),
            weekly_financial_scrape.subprocess.TimeoutExpired("scraper", 1),
            ("private-child-stdout", "private-child-stderr"),
        ]
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
            ),
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
            process.communicate.call_args_list,
            [
                call(timeout=1),
                call(timeout=weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS),
                call(),
            ],
        )

    def test_run_command_kills_process_group_when_wrapper_is_interrupted(self):
        process = Mock(pid=9753, returncode=None)
        process.poll.return_value = None
        process.communicate.side_effect = [
            KeyboardInterrupt(),
            ("private-child-stdout", "private-child-stderr"),
        ]
        with (
            patch.object(
                weekly_financial_scrape.subprocess,
                "Popen",
                return_value=process,
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

    def test_run_command_cleans_child_when_signal_arrives_during_spawn(self):
        process = Mock(pid=8642, returncode=None)
        process.communicate.return_value = (
            "private-child-stdout",
            "private-child-stderr",
        )

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
        process.communicate.assert_called_once_with(
            timeout=weekly_financial_scrape.PROCESS_GROUP_GRACE_SECONDS
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

    def test_main_reports_already_running_without_starting_work(self):
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
            patch.object(weekly_financial_scrape, "_run_locked") as run_locked,
        ):
            returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 0)
        self.assertEqual(json.loads(output), {"status": "already_running"})
        run_locked.assert_not_called()

    def test_main_never_echoes_captured_child_output(self):
        source = weekly_financial_scrape.Source(
            "fixture_source",
            ("fixture_scraper.py",),
            ("update_data.py", "import-json-fixture"),
        )
        private_markers = ("private-child-stdout", "private-child-stderr")
        calls = []

        def fake_run(arguments, env, timeout=weekly_financial_scrape.COMMAND_TIMEOUT_SECONDS):
            calls.append((arguments, dict(env)))
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
        }
        boa_calls = []

        def fake_boa(run_id, env, op_env=None):
            boa_calls.append((run_id, dict(env), dict(op_env or {})))
            return boa_result

        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            repo = temp_path / "repo"
            python = repo / "venv" / "bin" / "python3"
            token_file = temp_path / ".env-token"
            python.parent.mkdir(parents=True)
            python.touch()
            token_file.write_text("private-service-token", encoding="utf-8")

            with (
                patch.object(sys, "argv", [str(MODULE_PATH)]),
                patch.object(weekly_financial_scrape, "REPO", repo),
                patch.object(weekly_financial_scrape, "PYTHON", python),
                patch.object(weekly_financial_scrape, "OP_TOKEN_FILE", token_file),
                patch.object(
                    weekly_financial_scrape,
                    "LOCK_PATH",
                    temp_path / "state" / ".weekly-scrape.lock",
                ),
                patch.object(weekly_financial_scrape, "SOURCES", (source,)),
                patch.object(weekly_financial_scrape, "run_command", side_effect=fake_run),
                patch.object(
                    weekly_financial_scrape,
                    "run_boa",
                    side_effect=fake_boa,
                ),
                patch.object(weekly_financial_scrape.uuid, "uuid4", return_value=self.RUN_ID),
                patch.dict(
                    os.environ,
                    {
                        "OP_SERVICE_ACCOUNT_TOKEN": "stale-parent-token",
                        "SCRAPER_USER": "stale-parent-user",
                        "SCRAPER_PW": "stale-parent-password",
                    },
                    clear=False,
                ),
            ):
                returncode, output = self.capture_stdout(weekly_financial_scrape.main)

        self.assertEqual(returncode, 0)
        for marker in private_markers:
            self.assertNotIn(marker, output)
        self.assertEqual(len(calls), 2)
        for _, child_env in calls:
            self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", child_env)
            self.assertNotIn("SCRAPER_USER", child_env)
            self.assertNotIn("SCRAPER_PW", child_env)
        self.assertEqual(len(boa_calls), 1)
        boa_child_env = boa_calls[0][1]
        boa_op_env = boa_calls[0][2]
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", boa_child_env)
        self.assertNotIn("SCRAPER_USER", boa_child_env)
        self.assertNotIn("SCRAPER_PW", boa_child_env)
        self.assertNotIn("SCRAPER_USER", boa_op_env)
        self.assertNotIn("SCRAPER_PW", boa_op_env)
        self.assertEqual(
            boa_op_env["OP_SERVICE_ACCOUNT_TOKEN"],
            "private-service-token",
        )
        payloads = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(payloads[0]["source"], "fixture_source")
        self.assertEqual(payloads[1]["source"], "boa")
        self.assertEqual(payloads[2]["status"], "ok")
        self.assertEqual(payloads[2]["run_id"], self.RUN_ID)


if __name__ == "__main__":
    unittest.main()
