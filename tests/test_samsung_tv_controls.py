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
