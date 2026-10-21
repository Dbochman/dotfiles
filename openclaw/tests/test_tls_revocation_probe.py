import importlib.util
from pathlib import Path
import ssl
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location("tls_probe", Path(__file__).resolve().parents[1] / "bin/tls-revocation-probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TLSProbeTests(unittest.TestCase):
    def test_covers_all_twelve_chains(self):
        self.assertEqual(len(probe.CANARIES), 12)
        self.assertEqual(len(set(probe.CANARIES.values())), 6)

    def test_certificate_rejection_is_pass_only_for_canary(self):
        error = ssl.SSLCertVerificationError()
        error.verify_code = 20
        with mock.patch.object(probe.socket, "create_connection", side_effect=error):
            self.assertEqual(probe.probe("test", "expected", None)["result"], "rejected")
            self.assertEqual(probe.probe("test", None, None)["result"], "inconclusive")

    def test_expired_certificate_does_not_prove_ca_distrust(self):
        error = ssl.SSLCertVerificationError()
        error.verify_code = 10
        with mock.patch.object(probe.socket, "create_connection", side_effect=error):
            self.assertEqual(probe.probe("test", "expected", None)["result"], "inconclusive")

    def test_network_failure_is_never_protection(self):
        with mock.patch.object(probe.socket, "create_connection", side_effect=TimeoutError()):
            result = probe.probe("test", "expected", None)
        self.assertEqual(probe.exit_code([result]), 2)

    def test_changed_canary_is_not_a_confirmed_exposure(self):
        context = mock.MagicMock()
        context.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = {
            "issuer": ((('commonName', 'different issuer'),),)
        }
        with mock.patch.object(probe.socket, "create_connection"):
            result = probe.probe("test", "expected", context)
        self.assertEqual(result["result"], "canary_changed")
        self.assertEqual(probe.exit_code([result]), 2)

    def test_exposure_wins_over_inconclusive_result(self):
        self.assertEqual(probe.exit_code([{"result": "exposed"}, {"result": "inconclusive"}]), 1)
        self.assertEqual(probe.exit_code([{"result": "rejected"}, {"result": "control_passed"}]), 0)
        self.assertEqual(probe.exit_code([]), 2)
