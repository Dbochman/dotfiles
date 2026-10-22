import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "readiness", Path(__file__).parents[1] / "bin/github-runner-readiness.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)
NOW = 1_800_000_000


def fetcher(version="24.21.0", pr="open", **release_overrides):
    release = {"tag_name": "v2.339.0", "draft": False, "prerelease": False, **release_overrides}
    source = base64.b64encode(f'NODE24_VERSION="{version}"\n'.encode()).decode()
    mapping = {
        f"repos/{monitor.REPO}/pulls/339": {"state": pr},
        "repos/actions/runner/releases/latest": release,
        "repos/actions/runner/commits/v2.339.0": {"sha": "a" * 40},
        "repos/actions/runner/contents/src/Misc/externals.sh?ref=" + "a" * 40:
            {"encoding": "base64", "content": source},
    }
    return Mock(side_effect=lambda path: mapping[path])


class ReadinessTests(unittest.TestCase):
    def test_old_release_waits(self):
        send = Mock()
        state = monitor.tick({}, NOW, fetcher(), send)
        self.assertEqual(state["status"], "waiting")
        self.assertFalse(state["evidence"]["patched_source"])
        send.assert_called_once_with("started", state["evidence"])

    def test_floors(self):
        for version, ready in [("24.22.0", False), ("24.22.1", True), ("24.23.0", True)]:
            with self.subTest(version=version):
                self.assertEqual(monitor.inspect_release(fetcher(version))["patched_source"], ready)

    def test_rejects_unknown_versions(self):
        for version in ["25.0.0", "26.12.1", "24.22.1-rc.1", "invalid", '24.22.1"\nNODE24_VERSION="24.21.0']:
            with self.subTest(version=version), self.assertRaises(ValueError):
                monitor.inspect_release(fetcher(version))

    def test_rejects_nonstable_release(self):
        for override in [{"draft": True}, {"prerelease": True}, {"tag_name": "main"}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                monitor.inspect_release(fetcher(**override))

    def test_candidate_notifies_once_and_stops_polling(self):
        fetch, send = fetcher("24.22.1"), Mock()
        state = monitor.tick({}, NOW, fetch, send)
        self.assertEqual(state["status"], "candidate")
        fetch.reset_mock()
        monitor.tick(state, NOW + 4000, fetch, send)
        fetch.assert_not_called()
        send.assert_called_once()

    def test_hourly_limit(self):
        fetch, send = fetcher(), Mock()
        state = monitor.tick({}, NOW, fetch, send)
        fetch.reset_mock()
        monitor.tick(state, NOW + 60, fetch, send)
        fetch.assert_not_called()
        send.assert_called_once()

    def test_expiry_does_not_contact_release_api(self):
        fetch, send = Mock(), Mock()
        state = monitor.tick({"started_at": NOW}, NOW + monitor.WINDOW, fetch, send)
        self.assertEqual(state["status"], "expired")
        fetch.assert_not_called()
        send.assert_called_once_with("expired", {})

    def test_closed_pr_stops(self):
        fetch, send = fetcher(pr="closed"), Mock()
        state = monitor.tick({}, NOW, fetch, send)
        self.assertEqual(state["status"], "closed")
        self.assertEqual(fetch.call_count, 1)
        send.assert_not_called()

    def test_transient_failure_not_ready_or_cleared(self):
        fetch, send = Mock(side_effect=TimeoutError("private detail")), Mock()
        state = monitor.tick({}, NOW, fetch, send)
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(state["error"], "TimeoutError")
        self.assertNotIn("private detail", json.dumps(state))
        send.assert_not_called()

    def test_three_failures_alert_once(self):
        fetch, send = Mock(side_effect=TimeoutError()), Mock()
        state = {}
        for index in range(4):
            state = monitor.tick(state, NOW + index * 3600, fetch, send)
        send.assert_called_once_with("errors", {})

    def test_recovery_resets_failure_count(self):
        state = monitor.tick({}, NOW, Mock(side_effect=TimeoutError()), Mock())
        state = monitor.tick(state, NOW + 3600, fetcher(), Mock())
        self.assertEqual(state["failures"], 0)
        self.assertNotIn("error", state)

    def test_failed_notification_retries_bounded(self):
        send = Mock(side_effect=TimeoutError())
        state = monitor.tick({}, NOW, fetcher("24.22.1"), send)
        self.assertEqual(state["notifications"], [])
        self.assertEqual(state["notification_error"], "TimeoutError")
        send.reset_mock()
        state = monitor.tick(state, NOW + 86400, Mock(), send)
        send.assert_not_called()
        self.assertEqual(state["notification_error"], "retry_window_expired")

    def test_existing_comment_reconciles_without_post(self):
        comment = {"body": "<!-- runner-readiness-339-candidate -->", "user": {"login": "Dbochman"}}
        with patch.object(monitor, "gh", return_value=json.dumps([[comment]])) as gh:
            monitor.notify("candidate", {})
        self.assertEqual(gh.call_count, 1)

    def test_notification_uses_stdin_and_fixed_target(self):
        with patch.object(monitor, "gh", side_effect=["[[]]", "receipt"]) as gh:
            monitor.notify("started", {})
        self.assertEqual(gh.call_args.args[0], ["pr", "comment", "339", "--repo", monitor.REPO, "--body-file", "-"])

    def test_atomic_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            monitor.save(path, {"status": "waiting"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {"status": "waiting"})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
