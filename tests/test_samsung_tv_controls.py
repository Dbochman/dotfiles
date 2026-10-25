"""Short remote batches must not retry partial navigation or accept power toggles."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import io
import unittest
from unittest.mock import MagicMock, patch


loader = SourceFileLoader("samsung_tv", str(Path(__file__).resolve().parents[1] / "bin/samsung-tv"))
tv = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(tv)


class PowerOnTests(unittest.TestCase):
    def run_power(self, states):
        remote = MagicMock()
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        with patch.object(tv, "read_power", side_effect=states), \
                patch.object(tv, "get_tv", return_value=remote) as connect, \
                patch.object(tv, "send_wake") as wake, \
                patch.object(tv.time, "sleep", side_effect=sleep), \
                patch.object(tv.time, "monotonic", side_effect=lambda: now[0]):
            result = tv.ensure_power_on("Frame", {"ip": "test"})
        self.elapsed = now[0]
        return result, remote, connect, wake

    def test_already_on_never_connects_or_wakes(self):
        result, remote, connect, wake = self.run_power(["on"])
        self.assertTrue(result["power_on_verified"])
        connect.assert_not_called()
        wake.assert_not_called()
        remote.send_key.assert_not_called()

    def test_connection_itself_wakes_panel_without_toggle(self):
        result, remote, connect, wake = self.run_power(["standby", "on"])
        self.assertTrue(result["power_on_verified"])
        remote.send_key.assert_not_called()
        remote.close.assert_called_once()

    def test_standby_gets_one_key_and_verified_poll(self):
        result, remote, connect, wake = self.run_power(["standby", "standby", "on"])
        self.assertTrue(result["power_on_verified"])
        self.assertTrue(result["power_key_sent"])
        remote.send_key.assert_called_once_with("KEY_POWER")
        remote.close.assert_called_once()

    def test_unknown_after_connection_does_not_toggle(self):
        result, remote, connect, wake = self.run_power(["standby", "unknown"])
        self.assertFalse(result["power_on_verified"])
        remote.send_key.assert_not_called()
        remote.close.assert_called_once()

    def test_lan_wake_followed_by_remote_fallback(self):
        result, remote, connect, wake = self.run_power(
            ["unknown", "standby", "standby", "on"])
        self.assertTrue(result["power_on_verified"])
        wake.assert_called_once()
        remote.send_key.assert_called_once_with("KEY_POWER")
        self.assertEqual(self.elapsed, 0)

    def test_cold_wake_waits_for_delayed_standby_then_uses_one_key(self):
        result, remote, connect, wake = self.run_power(
            ["unknown"] + ["unknown"] * 32 + ["standby", "standby", "on"])
        self.assertTrue(result["power_on_verified"])
        self.assertEqual(self.elapsed, 64)
        wake.assert_called_once()
        connect.assert_called_once()
        remote.send_key.assert_called_once_with("KEY_POWER")
        remote.close.assert_called_once()

    def test_cold_wake_reaches_on_without_remote_or_toggle(self):
        result, remote, connect, wake = self.run_power(
            ["unknown"] + ["unknown"] * 32 + ["on"])
        self.assertTrue(result["power_on_verified"])
        wake.assert_called_once()
        connect.assert_not_called()
        remote.send_key.assert_not_called()

    def test_unreachable_after_wake_never_toggles(self):
        result, remote, connect, wake = self.run_power(["unknown"] * 100)
        self.assertFalse(result["power_on_verified"])
        self.assertEqual(self.elapsed, tv.WAKE_READY_TIMEOUT)
        wake.assert_called_once()
        connect.assert_not_called()

    def test_failed_toggle_is_not_repeated(self):
        result, remote, connect, wake = self.run_power(["standby"] * 8)
        self.assertFalse(result["power_on_verified"])
        remote.send_key.assert_called_once_with("KEY_POWER")

    def test_send_failure_closes_and_does_not_retry(self):
        remote = MagicMock()
        remote.send_key.side_effect = RuntimeError("disconnected")
        with patch.object(tv, "read_power", return_value="standby"), \
                patch.object(tv, "get_tv", return_value=remote):
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                tv.ensure_power_on("Frame", {"ip": "test"})
        remote.send_key.assert_called_once()
        remote.close.assert_called_once()


class StatusTests(unittest.TestCase):
    def test_unreachable_during_client_initialization_is_reported(self):
        # This client reads model metadata in its constructor, before the
        # explicit REST call. A sleeping TV must still produce normal status.
        library = MagicMock()
        library.SamsungTVWS.side_effect = RuntimeError("connection refused")
        with patch.dict(tv.sys.modules, {"samsungtvws": library}), \
                patch.object(tv, "load_tvs", return_value={"Frame": {"ip": "test"}}), \
                patch.object(tv, "token_path", return_value="unused"), \
                patch.object(tv.sys, "stdout", new_callable=io.StringIO) as output:
            tv.cmd_status([])
        self.assertIn("Power:  UNREACHABLE", output.getvalue())


class RemoteBatchTests(unittest.TestCase):
    def test_bounded_navigation_uses_one_connection_and_closes_it(self):
        remote = MagicMock()
        with patch.object(tv, "get_tv", return_value=remote) as connect, \
                patch.object(tv.sys, "stdout", new_callable=io.StringIO):
            tv.cmd_keys("Frame", {"ip": "test"}, ["left", "KEY_ENTER"])
        connect.assert_called_once()
        self.assertEqual([c.args[0] for c in remote.send_key.call_args_list], ["KEY_LEFT", "KEY_ENTER"])
        remote.close.assert_called_once()

    def test_invalid_batch_never_connects(self):
        for keys in ([], ["UP"] * 17, ["POWER"], ["MUTE"], ["not-a-key"]):
            with self.subTest(keys=keys), patch.object(tv, "get_tv") as connect, \
                    patch.object(tv.sys, "stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    tv.cmd_keys("Frame", {"ip": "test"}, keys)
                connect.assert_not_called()

    def test_partial_failure_closes_without_retrying(self):
        remote = MagicMock()
        remote.send_key.side_effect = [None, RuntimeError("disconnected")]
        with patch.object(tv, "get_tv", return_value=remote), \
                patch.object(tv.sys, "stderr", new_callable=io.StringIO) as output:
            with self.assertRaises(RuntimeError):
                tv.cmd_keys("Frame", {"ip": "test"}, ["LEFT", "ENTER", "DOWN"])
            self.assertIn("after 1 sends", output.getvalue())
        self.assertEqual(remote.send_key.call_count, 2)
        remote.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
