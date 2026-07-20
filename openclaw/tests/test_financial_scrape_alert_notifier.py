"""Tests for the delivery-only weekly financial scrape alert notifier."""

from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
MODULE_PATH = BIN_DIR / "financial-scrape-alert-notifier.py"
SPEC = importlib.util.spec_from_file_location(
    "financial_scrape_alert_notifier",
    MODULE_PATH,
)
notifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = notifier
SPEC.loader.exec_module(notifier)


class FinancialScrapeAlertNotifierTests(unittest.TestCase):
    RUN_ID = "11111111-2222-3333-4444-555555555555"
    NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def alert(cls, **overrides):
        payload = {
            "contract": 2,
            "run_id": cls.RUN_ID,
            "status": "failed",
            "run_status": None,
            "created_at": "2026-07-19T11:00:00+00:00",
            "reason": None,
            "missing_profiles": [],
            "signal": None,
            "affected": [
                {
                    "source": "bwsc",
                    "states": {
                        "scrape": "failed",
                        "import": "skipped",
                        "path": "not_observed",
                    },
                }
            ],
            "attempts": 0,
            "next_attempt_at": "2026-07-19T11:00:00+00:00",
            "last_attempt_at": None,
            "last_error": None,
            "delivery_state": "pending",
            "sent_at": None,
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def private_outbox(root):
        state = root / "state"
        state.mkdir(mode=0o700)
        outbox = state / "outbox"
        outbox.mkdir(mode=0o700)
        return outbox

    @classmethod
    def write_alert(cls, outbox, payload=None):
        payload = cls.alert() if payload is None else payload
        path = outbox / f"{payload['run_id']}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    @staticmethod
    def capture_main(arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            result = notifier.main(arguments)
        return result, json.loads(output.getvalue())

    def test_alert_validation_is_exact_and_bounded(self):
        self.assertTrue(notifier.validate_alert(self.alert(), self.RUN_ID))
        invalid = []
        for key in self.alert():
            missing = self.alert()
            missing.pop(key)
            invalid.append(missing)
        invalid.extend([
            self.alert(extra="forbidden"),
            self.alert(status="ok"),
            self.alert(run_id="not-a-run-id"),
            self.alert(attempts=True),
            self.alert(last_error="provider-private-output"),
            self.alert(affected=[{"source": "private", "states": {}}]),
        ])
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertFalse(notifier.validate_alert(payload))

    def test_weekly_wrapper_alert_contract_matches_notifier(self):
        weekly = runpy.run_path(str(BIN_DIR / "weekly-financial-scrape.py"))
        for reason in (None, "tesla_configuration_unavailable"):
            payload = weekly["build_alert_payload"](
                "failed",
                self.RUN_ID,
                "2026-07-19T11:00:00+00:00",
                reason=reason,
                results=[
                    {
                        "source": "bwsc",
                        "scrape": "failed",
                        "reauth": "not_needed",
                        "import": "skipped",
                        "path": "not_observed",
                    }
                ],
            )

            self.assertTrue(notifier.validate_alert(payload, self.RUN_ID))

    def test_message_is_deterministic_redacted_and_omits_run_id(self):
        first = notifier.format_message(self.alert())
        second = notifier.format_message(self.alert())
        self.assertEqual(first, second)
        self.assertIn("Weekly financial scrape failed", first)
        self.assertIn("bwsc", first)
        self.assertNotIn(self.RUN_ID, first)
        self.assertNotIn("account", first.lower())

    def test_message_formatter_is_total_for_maximum_valid_schema(self):
        longest = {
            field: max(values, key=len)
            for field, values in notifier.STATE_VALUES.items()
        }
        alert = self.alert(
            reason="scraper_contract_mismatch",
            missing_profiles=["boa", "bwsc", "eversource", "national_grid", "pennymac"],
            signal="termination",
            affected=[
                {"source": source, "states": dict(longest)}
                for source in sorted(notifier.SOURCES)
            ],
        )

        self.assertTrue(notifier.validate_alert(alert))
        message = notifier.format_message(alert)
        self.assertLessEqual(len(message.encode("utf-8")), notifier.MESSAGE_MAX_BYTES)
        self.assertIn("Affected sources:", message)

    def test_empty_queue_never_resolves_target_or_sends(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            with (
                patch.object(notifier, "resolve_chat_id") as resolve,
                patch.object(notifier, "send_imessage") as send,
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["due"], 0)
        resolve.assert_not_called()
        send.assert_not_called()

    def test_confirmed_send_deletes_alert_only_after_success(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            observations = []

            def successful_send(chat_id, message):
                state = json.loads(path.read_text(encoding="utf-8"))["delivery_state"]
                observations.append((chat_id, message, state))

            with (
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage", side_effect=successful_send),
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            self.assertEqual(observations[0][0], "12345")
            self.assertEqual(observations[0][2], "inflight")
            self.assertFalse(path.exists())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["sent"], 1)

    def test_failed_send_is_retained_with_bounded_exponential_backoff(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            with (
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(
                    notifier,
                    "send_imessage",
                    side_effect=notifier.DeliveryError("send_failed"),
                ),
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            retained = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(retained["delivery_state"], "pending")
            self.assertEqual(retained["attempts"], 1)
            self.assertEqual(retained["last_error"], "send_failed")
            self.assertEqual(
                retained["next_attempt_at"],
                "2026-07-19T12:15:00+00:00",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result["status"], "retry_pending")
            self.assertEqual(result["retained"], 1)

        self.assertLessEqual(
            notifier._backoff_seconds(100_000),
            notifier.MAX_BACKOFF_SECONDS,
        )

    def test_target_failure_is_safe_and_retains_each_due_alert(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            with (
                patch.object(
                    notifier,
                    "resolve_chat_id",
                    side_effect=notifier.TargetError,
                ),
                patch.object(notifier, "send_imessage") as send,
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            retained = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(retained["last_error"], "target_unavailable")
            self.assertEqual(result["status"], "retry_pending")
            send.assert_not_called()

    def test_invalid_or_insecure_queue_entry_is_never_sent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            path.chmod(0o644)
            with (
                patch.object(notifier, "resolve_chat_id") as resolve,
                patch.object(notifier, "send_imessage") as send,
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["quarantined"], 1)
        resolve.assert_not_called()
        send.assert_not_called()

    def test_only_exact_private_producer_temporary_is_deferred(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            outbox = self.private_outbox(root)
            producer_temp = outbox / f".alert.{self.RUN_ID}.tmp"
            producer_temp.write_bytes(b"")
            producer_temp.chmod(0o600)

            insecure_temp = outbox / (
                ".alert.22222222-3333-4444-5555-666666666666.tmp"
            )
            insecure_temp.write_bytes(b"")
            insecure_temp.chmod(0o644)
            for name in (".alert.not-a-run-id.tmp", ".delivery.abcdefgh.tmp"):
                invalid = outbox / name
                invalid.write_text("invalid\n", encoding="utf-8")
                invalid.chmod(0o600)

            with (
                patch.object(notifier, "resolve_chat_id") as resolve,
                patch.object(notifier, "send_imessage") as send,
            ):
                result = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["deferred"], 1)
            self.assertEqual(result["invalid"], 3)
            self.assertEqual(result["quarantined"], 3)
            self.assertTrue(producer_temp.exists())
            resolve.assert_not_called()
            send.assert_not_called()

    def test_producer_hardlink_overlap_preserves_deliverable_alert(self):
        weekly = runpy.run_path(str(BIN_DIR / "weekly-financial-scrape.py"))
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            outbox = self.private_outbox(root)
            alert = weekly["build_alert_payload"](
                "failed",
                self.RUN_ID,
                "2026-07-19T11:00:00+00:00",
                results=[
                    {
                        "source": "bwsc",
                        "scrape": "failed",
                        "reauth": "not_needed",
                        "import": "skipped",
                        "path": "not_observed",
                    }
                ],
            )
            linked = threading.Event()
            release = threading.Event()
            producer_result = {}
            real_link = os.link

            def paused_link(source, destination, **kwargs):
                result = real_link(source, destination, **kwargs)
                linked.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("producer overlap test timed out")
                return result

            def produce():
                try:
                    producer_result["created"] = weekly["enqueue_alert"](
                        alert,
                        outbox,
                    )
                except BaseException as error:
                    producer_result["error"] = error

            thread = threading.Thread(target=produce, daemon=True)
            with patch.object(weekly["os"], "link", side_effect=paused_link):
                thread.start()
                try:
                    self.assertTrue(linked.wait(timeout=5))
                    with (
                        patch.object(notifier, "resolve_chat_id") as resolve,
                        patch.object(notifier, "send_imessage") as send,
                    ):
                        overlap = notifier.deliver_pending(
                            outbox=outbox,
                            environment={},
                            now=self.NOW,
                        )
                    self.assertEqual(overlap["status"], "ok")
                    self.assertEqual(overlap["deferred"], 2)
                    self.assertEqual(overlap["invalid"], 0)
                    self.assertEqual(overlap["quarantined"], 0)
                    resolve.assert_not_called()
                    send.assert_not_called()
                finally:
                    release.set()
                    thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertNotIn("error", producer_result)
            self.assertTrue(producer_result.get("created"))
            path = outbox / f"{self.RUN_ID}.json"
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertFalse(
                (root / "state" / notifier.QUARANTINE_DIR.name).exists()
            )

            with (
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage") as send,
            ):
                delivered = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            self.assertEqual(delivered["status"], "ok")
            self.assertEqual(delivered["sent"], 1)
            self.assertEqual(send.call_count, 1)
            self.assertIn("bwsc", send.call_args.args[1])
            self.assertFalse(path.exists())

    def test_invalid_entries_over_scan_cap_are_quarantined_without_permanent_starvation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            outbox = self.private_outbox(root)
            for index in range(4):
                invalid = outbox / f"invalid-{index:02d}.tmp"
                invalid.write_text("unsafe\n", encoding="utf-8")
                invalid.chmod(0o600)
            path = self.write_alert(outbox)
            with (
                patch.object(notifier, "MAX_SCAN_ENTRIES", 3),
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage") as send,
            ):
                first = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )
                second = notifier.deliver_pending(
                    outbox=outbox,
                    environment={},
                    now=self.NOW,
                )

            self.assertEqual(first["overflow"], 1)
            self.assertGreaterEqual(first["quarantined"] + second["quarantined"], 4)
            self.assertEqual(send.call_count, 1)
            self.assertFalse(path.exists())
            quarantine = root / "state" / notifier.QUARANTINE_DIR.name
            self.assertEqual(quarantine.stat().st_mode & 0o777, 0o700)

    def test_deferred_front_entries_cannot_starve_later_due_alert(self):
        class StableScandir:
            def __init__(self, directory, ordered_names):
                self.directory = Path(directory)
                self.ordered_names = ordered_names

            def __enter__(self):
                return iter(
                    type("Entry", (), {"name": name})()
                    for name in self.ordered_names
                    if (self.directory / name).exists()
                )

            def __exit__(self, _type, _value, _traceback):
                return False

        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            ordered_names = []
            for index in range(4):
                run_id = f"00000000-0000-4000-8000-{index + 1:012d}"
                path = self.write_alert(
                    outbox,
                    self.alert(
                        run_id=run_id,
                        next_attempt_at="2026-07-20T12:00:00+00:00",
                    ),
                )
                ordered_names.append(path.name)
            due_path = self.write_alert(
                outbox,
                self.alert(run_id="00000000-0000-4000-8000-000000000005"),
            )
            ordered_names.append(due_path.name)

            def stable_scandir(directory):
                return StableScandir(directory, ordered_names)

            with (
                patch.object(notifier, "MAX_SCAN_ENTRIES", 2),
                patch.object(notifier.os, "scandir", side_effect=stable_scandir),
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage") as send,
            ):
                results = [
                    notifier.deliver_pending(
                        outbox=outbox,
                        environment={},
                        now=self.NOW,
                    )
                    for _attempt in range(3)
                ]

            self.assertTrue(all(result["scanned"] <= 2 for result in results))
            self.assertEqual([result["due"] for result in results], [0, 0, 1])
            self.assertEqual(send.call_count, 1)
            self.assertFalse(due_path.exists())
            cursor_path = outbox.parent / notifier.SCAN_CURSOR_NAME
            self.assertEqual(cursor_path.stat().st_mode & 0o777, 0o600)

    def test_sent_state_prevents_resend_when_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            real_delete = notifier.delete_confirmed
            failed_once = False

            def fail_first_cleanup(record):
                nonlocal failed_once
                if not failed_once:
                    failed_once = True
                    raise notifier.QueueError
                return real_delete(record)

            with (
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage") as send,
                patch.object(notifier, "delete_confirmed", side_effect=fail_first_cleanup),
            ):
                first = notifier.deliver_pending(
                    outbox=outbox, environment={}, now=self.NOW
                )
                retained = json.loads(path.read_text(encoding="utf-8"))
                second = notifier.deliver_pending(
                    outbox=outbox, environment={}, now=self.NOW
                )

            self.assertEqual(first["sent"], 1)
            self.assertEqual(first["sent_cleanup_failed"], 1)
            self.assertEqual(retained["delivery_state"], "sent")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(second["sent"], 0)
            self.assertFalse(path.exists())

    def test_state_write_failure_after_send_gets_backoff_not_immediate_retry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            with (
                patch.object(notifier, "resolve_chat_id", return_value="12345"),
                patch.object(notifier, "send_imessage") as send,
                patch.object(notifier, "mark_sent", side_effect=notifier.QueueError),
            ):
                first = notifier.deliver_pending(
                    outbox=outbox, environment={}, now=self.NOW
                )
                retained = json.loads(path.read_text(encoding="utf-8"))
                second = notifier.deliver_pending(
                    outbox=outbox, environment={}, now=self.NOW
                )

            self.assertEqual(first["status"], "failed")
            self.assertEqual(retained["delivery_state"], "pending")
            self.assertEqual(retained["last_error"], "state_update_failed")
            self.assertEqual(second["due"], 0)
            self.assertEqual(send.call_count, 1)

    def test_scoped_chat_environment_avoids_secret_cache(self):
        with patch.object(notifier, "_read_verified_file") as read:
            self.assertEqual(
                notifier.resolve_chat_id({notifier.CHAT_ID_ENV: "12345"}),
                "12345",
            )
        read.assert_not_called()
        with self.assertRaises(notifier.TargetError):
            notifier.resolve_chat_id({notifier.CHAT_ID_ENV: "chat_id:12345"})

    def test_owner_only_secret_cache_reads_only_exact_chat_assignment(self):
        with tempfile.TemporaryDirectory() as tempdir:
            openclaw = Path(tempdir) / ".openclaw"
            openclaw.mkdir(mode=0o700)
            cache = openclaw / ".secrets-cache"
            cache.write_text(
                "UNRELATED_CACHE_SECRET=private\nDYLAN_CHAT_ID=12345\n",
                encoding="utf-8",
            )
            cache.chmod(0o600)
            result = notifier.resolve_chat_id({}, secrets_cache=cache)

        self.assertEqual(result, "12345")

    def test_secret_cache_rejects_duplicate_or_shell_syntax_assignments(self):
        for contents in (
            "DYLAN_CHAT_ID=12345\nDYLAN_CHAT_ID=67890\n",
            "export DYLAN_CHAT_ID=12345\n",
            "DYLAN_CHAT_ID='12345'\n",
        ):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tempdir:
                openclaw = Path(tempdir) / ".openclaw"
                openclaw.mkdir(mode=0o700)
                cache = openclaw / ".secrets-cache"
                cache.write_text(contents, encoding="utf-8")
                cache.chmod(0o600)
                with self.assertRaises(notifier.TargetError):
                    notifier.resolve_chat_id({}, secrets_cache=cache)

    def test_secret_cache_replacement_between_validation_and_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            openclaw = Path(tempdir) / ".openclaw"
            openclaw.mkdir(mode=0o700)
            cache = openclaw / ".secrets-cache"
            replacement = openclaw / ".replacement"
            cache.write_text("DYLAN_CHAT_ID=12345\n", encoding="utf-8")
            replacement.write_text("DYLAN_CHAT_ID=67890\n", encoding="utf-8")
            cache.chmod(0o600)
            replacement.chmod(0o600)
            real_open = os.open
            swapped = False

            def swap_then_open(path, flags, *args):
                nonlocal swapped
                if Path(path) == cache and not swapped:
                    swapped = True
                    os.replace(replacement, cache)
                return real_open(path, flags, *args)

            with (
                patch.object(notifier.os, "open", side_effect=swap_then_open),
                self.assertRaises(notifier.TargetError),
            ):
                notifier.resolve_chat_id({}, secrets_cache=cache)

    def test_queue_rewrite_and_delete_reject_replaced_inode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            outbox = self.private_outbox(Path(tempdir))
            path = self.write_alert(outbox)
            record = notifier.read_alert(path)
            replacement = outbox / ".replacement"
            replacement.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            replacement.chmod(0o600)
            os.replace(replacement, path)

            with self.assertRaises(notifier.QueueError):
                notifier.retain_failure(record, "send_failed", self.NOW)
            with self.assertRaises(notifier.QueueError):
                notifier.delete_confirmed(record)
            self.assertTrue(path.exists())

    def test_imsg_requires_exact_bridge_rpc_receipt_and_discards_output(self):
        valid = notifier.CommandCapture(
            returncode=0,
            stdout=(
                b'{"id":"financial-scrape-alert-send","jsonrpc":"2.0",'
                b'"result":{"ok":true,"transport":"bridge"}}'
            ),
            stderr=b"",
        )
        with patch.object(notifier, "run_bounded_command", return_value=valid) as run:
            notifier.send_imessage("12345", "safe message")
        self.assertEqual(run.call_args.args[0], ["/opt/homebrew/bin/imsg", "rpc"])
        self.assertEqual(run.call_args.args[2], 20)
        request = json.loads(run.call_args.kwargs["stdin_data"])
        self.assertEqual(
            request,
            {
                "jsonrpc": "2.0",
                "id": "financial-scrape-alert-send",
                "method": "send",
                "params": {
                    "chat_id": 12345,
                    "text": "safe message",
                    "transport": "bridge",
                },
            },
        )

        for result in (
            {"ok": False, "transport": "bridge"},
            {"ok": True},
            {"ok": True, "transport": "applescript"},
        ):
            invalid = notifier.CommandCapture(
                returncode=0,
                stdout=json.dumps({
                    "id": "financial-scrape-alert-send",
                    "jsonrpc": "2.0",
                    "result": result,
                }).encode("utf-8"),
                stderr=b"",
            )
            with (
                self.subTest(result=result),
                patch.object(notifier, "run_bounded_command", return_value=invalid),
                self.assertRaisesRegex(notifier.DeliveryError, "receipt_invalid"),
            ):
                notifier.send_imessage("12345", "safe message")

    def test_command_capture_sends_one_bounded_stdin_payload(self):
        payload = b'{"request":"safe"}\n'
        captured = notifier.run_bounded_command(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
            ],
            {"PATH": "/usr/bin:/bin"},
            5,
            stdin_data=payload,
        )

        self.assertEqual(captured.returncode, 0)
        self.assertEqual(captured.stdout, payload)
        self.assertFalse(captured.stderr)
        self.assertFalse(captured.timed_out)
        with self.assertRaises(ValueError):
            notifier.run_bounded_command(
                [sys.executable, "-c", "pass"],
                {"PATH": "/usr/bin:/bin"},
                5,
                stdin_data=b"x" * (notifier.MAX_COMMAND_BYTES + 1),
            )

    def test_command_capture_bounds_stdin_when_child_does_not_read(self):
        payload = b"x" * (1024 * 1024)
        with patch.object(notifier, "MAX_COMMAND_BYTES", len(payload)):
            captured = notifier.run_bounded_command(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                {"PATH": "/usr/bin:/bin"},
                0.1,
                stdin_data=payload,
            )

        self.assertTrue(captured.timed_out)

    def test_command_capture_bounds_output_and_kills_on_timeout(self):
        flooded = notifier.run_bounded_command(
            [
                sys.executable,
                "-c",
                f"import os; os.write(1, b'x' * {notifier.MAX_COMMAND_BYTES + 1})",
            ],
            {"PATH": "/usr/bin:/bin"},
            5,
        )
        self.assertTrue(flooded.output_rejected)
        self.assertLessEqual(len(flooded.stdout), notifier.MAX_COMMAND_BYTES)

        started = time.monotonic()
        timed_out = notifier.run_bounded_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            {"PATH": "/usr/bin:/bin"},
            0.05,
        )
        self.assertTrue(timed_out.timed_out)
        self.assertLess(time.monotonic() - started, 2)

    def test_descendant_holding_output_pipe_cannot_hang_capture(self):
        code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)'])"
        )
        started = time.monotonic()
        result = notifier.run_bounded_command(
            [sys.executable, "-c", code],
            {"PATH": "/usr/bin:/bin"},
            5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 2)

    def test_process_group_cleanup_kills_term_ignoring_descendant(self):
        descendant_script = (
            "import signal,time; "
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
        process = notifier.subprocess.Popen(
            [sys.executable, "-c", leader_script],
            stdout=notifier.subprocess.PIPE,
            stderr=notifier.subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        descendant_pid = None
        try:
            descendant_pid = int(process.stdout.readline(32).decode("ascii").strip())
            with (
                patch.object(notifier, "PROCESS_GROUP_GRACE_SECONDS", 0.2),
                patch.object(notifier, "PROCESS_GROUP_POLL_SECONDS", 0.01),
            ):
                notifier._terminate_process_group(process)

            self.assertIsNotNone(process.returncode)
            self.assertFalse(notifier._process_group_exists(process))
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
        finally:
            try:
                os.killpg(process.pid, notifier.signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except notifier.subprocess.TimeoutExpired:
                pass

    def test_main_converts_unexpected_failure_to_safe_json_and_health(self):
        with tempfile.TemporaryDirectory() as tempdir:
            state = Path(tempdir) / "state"
            with (
                patch.object(notifier, "LOCK_PATH", state / ".lock"),
                patch.object(notifier, "HEALTH_PATH", state / "health.json"),
                patch.object(notifier, "deliver_pending", side_effect=RuntimeError("private")),
            ):
                returncode, output = self.capture_main([])

            self.assertEqual(returncode, 1)
            self.assertEqual(output, {"contract": 2, "status": "internal_error"})
            self.assertNotIn("private", json.dumps(output))
            health = json.loads((state / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["status"], "internal_error")

    def test_queue_enumeration_error_becomes_safe_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            state = Path(tempdir) / "state"
            with (
                patch.object(notifier, "LOCK_PATH", state / ".lock"),
                patch.object(notifier, "HEALTH_PATH", state / "health.json"),
                patch.object(notifier, "OUTBOX_DIR", state / "outbox"),
                patch.object(notifier.os, "scandir", side_effect=OSError("private")),
            ):
                returncode, output = self.capture_main([])

            self.assertEqual(returncode, 1)
            self.assertEqual(output, {"contract": 2, "status": "queue_unavailable"})
            self.assertNotIn("private", json.dumps(output))

    def test_lock_overlap_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            state = Path(tempdir) / "state"
            lock = state / ".lock"
            with patch.object(notifier, "LOCK_PATH", lock):
                with notifier.notifier_lock(lock) as acquired:
                    self.assertTrue(acquired)
                    returncode, output = self.capture_main([])

            self.assertEqual(returncode, 1)
            self.assertEqual(output["status"], "already_running")

    def test_notifier_lock_rejects_hardlink_without_changing_target_mode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            state = Path(tempdir) / "state"
            state.mkdir(mode=0o700)
            victim = state / "victim"
            victim.write_text("unrelated", encoding="utf-8")
            victim.chmod(0o644)
            lock = state / ".lock"
            os.link(victim, lock)

            with self.assertRaises(notifier.QueueError):
                with notifier.notifier_lock(lock):
                    self.fail("unsafe hardlinked lock must not be acquired")

            self.assertEqual(victim.stat().st_mode & 0o777, 0o644)
            self.assertEqual(victim.read_text(encoding="utf-8"), "unrelated")

    def test_canary_is_delivery_only_and_never_reads_queue(self):
        with (
            patch.object(notifier, "resolve_chat_id", return_value="12345"),
            patch.object(notifier, "send_imessage") as send,
            patch.object(notifier, "prepare_outbox") as outbox,
        ):
            result = notifier.run_canary({})

        self.assertEqual(result["status"], "canary_sent")
        send.assert_called_once_with("12345", notifier.CANARY_TEXT)
        outbox.assert_not_called()

    def test_canary_main_emits_only_safe_operational_json(self):
        with patch.object(
            notifier,
            "run_canary",
            return_value={"contract": 2, "status": "canary_sent", "sent": 1},
        ):
            returncode, output = self.capture_main(["--canary"])

        self.assertEqual(returncode, 0)
        self.assertEqual(output, {"contract": 2, "status": "canary_sent", "sent": 1})


if __name__ == "__main__":
    unittest.main()
