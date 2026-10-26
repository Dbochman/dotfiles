"""Short remote batches must not retry partial navigation or accept power toggles."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import types
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
        with patch.object(tv, "device_info", side_effect=RuntimeError("connection refused")), \
                patch.object(tv, "load_tvs", return_value={"Frame": {"ip": "test"}}), \
                patch.object(tv.sys, "stdout", new_callable=io.StringIO) as output:
            tv.cmd_status([])
        self.assertIn("Power:  UNREACHABLE", output.getvalue())

    def test_transport_failure_is_not_reported_as_tv_offline(self):
        with patch.object(tv, "device_info", side_effect=tv.TransportError("SSH unavailable")):
            with self.assertRaises(tv.TransportError):
                tv.read_power({"ip": tv.CROSSTOWN_TV})


class TransportTests(unittest.TestCase):
    def tunnel(self):
        tunnel = tv.CrosstownTunnel()
        tunnel.process = MagicMock()
        tunnel.process.poll.return_value = None
        tunnel.ports = {8001: 51001, 8002: 51002}
        return tunnel

    def test_routes_rest_remote_and_art_preserving_tls_and_query(self):
        tunnel = self.tunnel()
        for scheme, port, path in (
                ("http", 8001, "/api/v2/"),
                ("https", 8002, "/api/v2/applications/123"),
                ("wss", 8002, "/api/v2/channels/samsung.remote.control?name=test&token=test"),
                ("wss", 8002, "/api/v2/channels/com.samsung.art-app?name=test&token=test")):
            with self.subTest(scheme=scheme, path=path):
                self.assertEqual(tunnel.route(f"{scheme}://{tv.CROSSTOWN_TV}:{port}{path}"),
                                 f"{scheme}://127.0.0.1:{tunnel.ports[port]}{path}")

    def test_unexpected_destination_and_dead_tunnel_fail_closed(self):
        tunnel = self.tunnel()
        for url in ("http://192.168.165.3:8001/api/v2/",
                    "http://192.168.165.2:1234/api/v2/"):
            with self.assertRaises(tv.TransportError):
                tunnel.route(url)
        tunnel.process.poll.return_value = 255
        with self.assertRaises(tv.TransportError):
            tunnel.route("http://192.168.165.2:8001/api/v2/")

    def test_all_control_commands_require_the_crosstown_transport(self):
        commands = ["status", "pair frame", "power frame on", "power frame off", "volume frame up",
                    "mute frame", "key frame ENTER", "keys frame UP ENTER",
                    "app frame list", "app frame launch netflix", "app frame close netflix",
                    "art frame", "art frame on", "art frame off", "art frame list",
                    "art frame select test"]
        for command in commands:
            with self.subTest(command=command), \
                    patch.object(tv.sys, "argv", ["samsung-tv", *command.split()]), \
                    patch.object(tv, "load_tvs", return_value=tv.DEFAULT_TVS), \
                    patch.object(tv, "tv_transport") as transport, \
                    patch.object(tv, "dispatch"):
                tv.main()
                transport.assert_called_once_with(True)

    def test_failed_ssh_start_cleans_owned_process_without_dispatch(self):
        process = MagicMock()
        process.poll.return_value = 255
        with patch.object(tv.sys, "argv", ["samsung-tv", "status"]), \
                patch.object(tv, "load_tvs", return_value=tv.DEFAULT_TVS), \
                patch.object(tv.subprocess, "Popen", return_value=process) as start, \
                patch.object(tv, "dispatch") as dispatch:
            with self.assertRaises(tv.TransportError):
                tv.main()
        dispatch.assert_not_called()
        args = start.call_args.args[0]
        self.assertIn(tv.CROSSTOWN_HOST, args)
        self.assertIn("ExitOnForwardFailure=yes", args)
        self.assertIn("ControlPath=none", args)
        self.assertEqual(start.call_args.kwargs["stdin"], subprocess.PIPE)
        process.stdin.close.assert_called_once()
        self.assertIsNone(tv._tunnel)

    def test_transport_is_cleaned_after_partial_operation_failure(self):
        tunnel = self.tunnel()
        with patch.object(tv, "CrosstownTunnel", return_value=tunnel), \
                patch.object(type(tunnel), "__enter__", return_value=tunnel):
            with self.assertRaisesRegex(RuntimeError, "partial send"):
                with tv.tv_transport(True):
                    self.assertIs(tv._tunnel, tunnel)
                    raise RuntimeError("partial send")
        tunnel.process.terminate.assert_called_once()
        tunnel.process.wait.assert_called_once_with(timeout=5)
        tunnel.process.stdin.close.assert_called_once()
        self.assertIsNone(tv._tunnel)

    def test_library_factories_route_constructor_rest_and_art(self):
        # Minimal library double exercises factory relationships without
        # requiring the optional samsungtvws package in offline test runners.
        class Connection:
            def __init__(self, host, port=8001, timeout=3, **kwargs):
                self.host, self.port, self.timeout = host, port, timeout
                self.token, self.token_file = None, kwargs.get("token_file")
                self.key_press_delay, self.name = 1, "OpenClaw"
                self._rest_api = None

            def _format_rest_url(self, route=""):
                protocol = "https" if self.port == 8002 else "http"
                return f"{protocol}://{self.host}:{self.port}/api/v2/{route}"

            def _format_websocket_url(self, app):
                return f"wss://{self.host}:{self.port}/api/v2/channels/{app}"

        class Remote(Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.constructor_url = self._get_rest_api()._format_rest_url()

        modules = {}
        for name, attribute, value in (("samsungtvws", "SamsungTVWS", Remote),
                                       ("samsungtvws.rest", "SamsungTVRest", Connection),
                                       ("samsungtvws.art", "SamsungTVArt", Connection)):
            module = types.ModuleType(name)
            setattr(module, attribute, value)
            modules[name] = module
        with patch.dict(sys.modules, modules), patch.object(tv, "_tunnel", self.tunnel()):
            remote_class, rest_class = tv.client_classes(tv.CROSSTOWN_TV)
            remote = remote_class(tv.CROSSTOWN_TV, port=8002)
            self.assertEqual(remote.constructor_url, "https://127.0.0.1:51002/api/v2/")
            self.assertTrue(remote._format_websocket_url("remote").startswith("wss://127.0.0.1:51002/"))
            art = remote.art()
            self.assertTrue(art._format_websocket_url("art").startswith("wss://127.0.0.1:51002/"))
            self.assertEqual(art._get_rest_api()._format_rest_url(), "https://127.0.0.1:51002/api/v2/")
            self.assertEqual(rest_class(tv.CROSSTOWN_TV)._format_rest_url(), "http://127.0.0.1:51001/api/v2/")
        with patch.dict(sys.modules, modules), patch.object(tv, "_tunnel", None):
            with self.assertRaises(tv.TransportError):
                tv.client_classes(tv.CROSSTOWN_TV)


class HandshakeTests(unittest.TestCase):
    def protocol(self, events):
        wire = MagicMock()
        wire.recv.side_effect = [json.dumps({"event": event}) for event in events]
        protocol = types.SimpleNamespace(
            websocket=types.SimpleNamespace(create_connection=MagicMock(return_value=wire)),
            ssl=types.SimpleNamespace(CERT_NONE=0),
            helper=types.SimpleNamespace(process_api_response=json.loads),
            IGNORE_EVENTS_AT_STARTUP=("ed.edenTV.update", "ms.voiceApp.hide"),
            MS_CHANNEL_UNAUTHORIZED="ms.channel.unauthorized",
            MS_CHANNEL_CONNECT_EVENT="ms.channel.connect",
            exceptions=types.SimpleNamespace(UnauthorizedError=PermissionError,
                                            ConnectionFailure=ConnectionError),
        )
        client = MagicMock()
        client.connection = None
        client.timeout = 5
        return protocol, wire, client

    def test_touch_notices_do_not_replace_authorization_acknowledgment(self):
        protocol, wire, client = self.protocol(
            ["ms.remote.touchDisable", "ms.remote.touchEnable", "ms.channel.connect"])
        with patch.dict(sys.modules, {"samsungtvws": types.SimpleNamespace(connection=protocol)}):
            self.assertIs(tv.open_websocket(client), wire)
        client._check_for_token.assert_called_once_with({"event": "ms.channel.connect"})
        self.assertIs(client.connection, wire)
        protocol.websocket.create_connection.assert_called_once()
        wire.close.assert_not_called()

    def test_unauthorized_or_unexpected_event_closes_without_retry(self):
        for event, exception in (("ms.channel.unauthorized", PermissionError),
                                 ("unexpected", ConnectionError)):
            protocol, wire, client = self.protocol(["ms.remote.touchEnable", event])
            with self.subTest(event=event), \
                    patch.dict(sys.modules, {"samsungtvws": types.SimpleNamespace(connection=protocol)}):
                with self.assertRaises(exception):
                    tv.open_websocket(client)
            wire.close.assert_called_once()
            client._check_for_token.assert_not_called()
            protocol.websocket.create_connection.assert_called_once()
            self.assertIsNone(client.connection)

    def test_continuous_notices_are_bounded_without_retry(self):
        protocol, wire, client = self.protocol(["ms.remote.touchDisable"] * 20)
        with patch.dict(sys.modules, {"samsungtvws": types.SimpleNamespace(connection=protocol)}):
            with self.assertRaisesRegex(TimeoutError, "event limit"):
                tv.open_websocket(client)
        self.assertEqual(wire.recv.call_count, 16)
        wire.close.assert_called_once()
        protocol.websocket.create_connection.assert_called_once()
        client._check_for_token.assert_not_called()

    def test_deadline_closes_before_reading_more_events(self):
        protocol, wire, client = self.protocol(["ms.channel.connect"])
        with patch.dict(sys.modules, {"samsungtvws": types.SimpleNamespace(connection=protocol)}), \
                patch.object(tv.time, "monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                tv.open_websocket(client)
        wire.recv.assert_not_called()
        wire.close.assert_called_once()
        client._check_for_token.assert_not_called()

    def test_pairing_is_one_longer_attempt_without_remote_keys(self):
        client = MagicMock()
        with patch.object(tv, "new_tv", return_value=client) as create, \
                patch.object(tv.sys, "stdout", new_callable=io.StringIO):
            tv.cmd_pair("Frame", {"ip": tv.CROSSTOWN_TV})
        create.assert_called_once_with(tv.CROSSTOWN_TV, "Frame", timeout=60)
        client.open.assert_called_once()
        client.close.assert_called_once()
        client.send_key.assert_not_called()

    def test_token_is_private_and_existing_value_is_preserved(self):
        with tempfile.TemporaryDirectory() as cache, patch.object(tv, "CACHE_DIR", cache):
            path = Path(tv.token_path("Frame"))
            path.write_text("test-token")
            path.chmod(0o644)
            tv.token_path("Frame")
            self.assertEqual(path.read_text(), "test-token")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)


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
