import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bluetooth"))
import airvalent_monitor as monitor
import airvalent_state as state


class AirvalentStateTests(unittest.TestCase):
    def sample(self, age=0):
        return {"co2_ppm": 660, "read_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
                "sample_interval_seconds": 300}

    def test_stale_and_failed_reads_do_not_masquerade_as_current(self):
        for cache in ({"sample": self.sample(901)},
                      {"sample": self.sample(), "last_error": "read_failed"},
                      {"sample": self.sample(-3600)}):
            with self.subTest(cache=cache):
                status = state.project_status(cache)
                self.assertFalse(status["online"])
                self.assertIsNone(status["co2_ppm"])
                self.assertIsNone(state.dashboard_room(status)["co2_ppm"])
        status = state.project_status({"sample": self.sample()})
        self.assertTrue(status["online"])
        self.assertEqual(status["co2_ppm"], 660)
        self.assertIsNone(status["measurement_at"])

    def test_decoder_rejects_truncated_and_implausible_values(self):
        self.assertEqual(monitor.decode_current(bytes([0x94, 0xc2, 0, 0, 0, 0, 0, 0])), 660)
        for raw in (b'', bytes(7), bytes(8), bytes([255])*8):
            with self.assertRaises(monitor.ReadError):
                monitor.decode_current(raw)

    def test_cache_projection_never_exposes_bindings(self):
        cache = {"sample": self.sample(), "api_key": "private-placeholder", "expected_address": 123}
        public = json.dumps(state.project_status(cache))
        self.assertNotIn("private-placeholder", public)
        self.assertNotIn("expected_address", public)

    def test_failed_collection_preserves_previous_sample_and_skips_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = self.sample()
            state.write_private(root / 'status.json', {"sample": old, "last_error": None})
            with patch.object(monitor, 'load_binding', return_value=({}, {})), \
                 patch.object(monitor, 'read_sensor', AsyncMock(side_effect=monitor.ReadError('sensor_unavailable'))), \
                 patch.object(monitor, 'append_sample') as append:
                result = monitor.collect(root)
            saved = state.private_json(root / 'status.json')
            self.assertFalse(result['ok'])
            self.assertEqual(saved['sample'], old)
            self.assertEqual(saved['last_error'], 'sensor_unavailable')
            append.assert_not_called()

    def test_lost_bond_stops_repeated_unattended_pairing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state.write_private(root / 'status.json', {"sample": self.sample(), "last_error": "reauth_required"})
            with patch.object(monitor, 'load_binding') as load:
                result = monitor.collect(root)
            self.assertEqual(result['error'], 'reauth_required')
            load.assert_not_called()

    def test_legacy_connection_timeout_latch_recovers_without_erasing_old_sample(self):
        for category in ('bond_unavailable', 'reauth_required'):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old = self.sample(3600)
                state.write_private(root/'status.json', {'sample':old, 'last_error':category,
                                                        'last_auth_failure_code':102})
                with patch.object(monitor, 'load_binding', return_value=({}, {})), \
                     patch.object(monitor, 'read_sensor', AsyncMock(side_effect=monitor.ReadError('sensor_unavailable'))):
                    self.assertEqual(monitor.collect(root)['error'], 'sensor_unavailable')
                saved = state.private_json(root/'status.json')
                self.assertEqual(saved['sample'], old)
                self.assertIsNotNone(saved['last_failure_at'])

    def test_rejected_or_unknown_authentication_stays_latched(self):
        for code in (None, 78, 81, 86):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state.write_private(root/'status.json', {'last_error':'reauth_required', 'last_auth_failure_code':code})
                with patch.object(monitor, 'load_binding') as load:
                    self.assertEqual(monitor.collect(root)['error'], 'reauth_required')
                    load.assert_not_called()

    def test_connection_timeout_retries_next_collection_and_appends_only_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(monitor, 'load_binding', return_value=({}, {})), \
                 patch.object(monitor, 'read_sensor', AsyncMock(side_effect=[
                     monitor.authentication_error('bond_unavailable', 102), self.sample()])), \
                 patch.object(monitor, 'append_sample') as append:
                self.assertEqual(monitor.collect(root)['error'], 'ble_connection_timeout')
                append.assert_not_called()
                self.assertEqual(state.cached_status(root/'status.json')['error'], 'ble_connection_timeout')
                self.assertTrue(monitor.collect(root)['ok'])
                append.assert_called_once()

    def test_overlapping_collector_does_not_read_the_sensor(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / '.collector.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(monitor, 'load_binding') as load:
                    self.assertEqual(monitor.collect(root)['outcome'], 'already_running')
                    load.assert_not_called()

    def test_live_read_survives_history_append_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(monitor, 'load_binding', return_value=({}, {})), \
                 patch.object(monitor, 'read_sensor', AsyncMock(return_value=self.sample())), \
                 patch.object(monitor, 'append_sample', side_effect=ValueError()):
                result = monitor.collect(root)
            status = state.cached_status(root / 'status.json')
            self.assertFalse(result['ok'])
            self.assertTrue(status['online'])
            self.assertFalse(status['history_ok'])

    def test_unsafe_binding_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'real.json').write_text('{}')
            (root/'connection.json').symlink_to(root/'real.json')
            with self.assertRaises(monitor.ReadError):
                monitor.load_binding(root)

    def test_transport_override_requires_private_lan_ipv4(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = {'host': monitor.BRIDGE + '.local', 'api_key': 'placeholder',
                   'expected_address': 123}
            state.write_private(root/'services.json', {'address':123, 'services':[
                {'uuid':monitor.SERVICE, 'characteristics':[
                    {'uuid':monitor.CURRENT, 'handle':1}, {'uuid':monitor.INTERVAL, 'handle':2}]}]})
            for host in ('127.0.0.1', '169.254.1.1', '8.8.8.8', '::1', 'example.com'):
                state.write_private(root/'connection.json', dict(cfg, transport_host=host))
                with self.subTest(host=host), self.assertRaises(monitor.ReadError):
                    monitor.load_binding(root)
            state.write_private(root/'connection.json', dict(cfg, transport_host='192.168.1.2'))
            self.assertEqual(monitor.load_binding(root)[0]['transport_host'], '192.168.1.2')


class AuthenticationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, recovery, accepted=True, error=None):
        requested = asyncio.Event()
        finished = asyncio.Future()
        calls = []
        async def pair(*args, **kwargs):
            requested.set()
            return await finished
        async def execute(action, payload):
            calls.append((action.name, payload))
            finished.set_result(SimpleNamespace(paired=accepted, error=error))
        cli = SimpleNamespace(bluetooth_device_pair=pair, execute_service=execute)
        claim = Mock()
        actions = [SimpleNamespace(name='airvalent_submit_passkey')]
        await monitor.authenticate(cli, 123, actions, requested, recovery, claim)
        return calls, claim

    async def test_requested_fixed_pin_is_submitted_once(self):
        calls, claim = await self.exercise({'pin': '000123'})
        self.assertEqual(calls, [('airvalent_submit_passkey', {'passkey': 123})])
        claim.assert_called_once()

    async def test_no_provisioned_pin_stops_at_the_request(self):
        with self.assertRaisesRegex(monitor.ReadError, '^reauth_required$'):
            await self.exercise(None)

    async def test_rejected_pin_is_a_latched_error(self):
        with self.assertRaisesRegex(monitor.ReadError, '^reauth_required$'):
            await self.exercise({'pin': '000123'}, accepted=False)

    async def test_link_loss_after_pin_submission_is_retryable(self):
        with self.assertRaisesRegex(monitor.ReadError, '^ble_connection_timeout$') as error:
            await self.exercise({'pin':'000123'}, accepted=False, error=102)
        self.assertEqual(error.exception.auth_failure_code, 102)

    async def test_link_loss_without_pin_request_is_retryable(self):
        cli = SimpleNamespace(bluetooth_device_pair=AsyncMock(return_value=SimpleNamespace(paired=False,error=102)))
        with self.assertRaisesRegex(monitor.ReadError, '^ble_connection_timeout$'):
            await monitor.authenticate(cli, 123, [], asyncio.Event())

    async def test_only_stale_bond_gets_one_reconnect(self):
        with patch.object(monitor, 'read_sensor', AsyncMock(side_effect=[monitor.ReadError('bond_unavailable'), {'co2_ppm': 660}])) as read, \
             patch.object(asyncio, 'sleep', AsyncMock()):
            result = await monitor.read_with_recovery({}, {}, {'pin':'000123'}, Mock())
            self.assertEqual(result['co2_ppm'], 660)
            self.assertEqual(read.await_count, 2)
        with patch.object(monitor, 'read_sensor', AsyncMock(side_effect=monitor.ReadError('reauth_required'))) as read:
            with self.assertRaises(monitor.ReadError):
                await monitor.read_with_recovery({}, {}, {'pin':'000123'}, Mock())
            self.assertEqual(read.await_count, 1)

    def test_recovery_binding_must_match_sensor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state.write_private(root / 'recovery.json', {'pin':'000123','bridge':monitor.BRIDGE,'expected_address':456})
            with self.assertRaisesRegex(monitor.ReadError, '^binding_invalid$'):
                monitor.load_recovery(root, {'expected_address':123})


class SensorTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_failure_still_closes_owned_tunnel(self):
        class Client:
            def __init__(self, *args, **kwargs):
                self.log = None
            async def connect(self, **kwargs): pass
            async def device_info(self): return SimpleNamespace(bluetooth_proxy_feature_flags=1)
            def subscribe_logs(self, callback, **kwargs): self.log = callback
            async def list_entities_services(self):
                return [], [SimpleNamespace(name='airvalent_bond_status')]
            async def execute_service(self, service, args):
                self.assert_action = service.name
                self.log(SimpleNamespace(message=b'[airvalent_pair] Stored BLE bonds now: 1'))
            def subscribe_bluetooth_le_raw_advertisements(self, callback):
                callback(SimpleNamespace(advertisements=[SimpleNamespace(address=123,address_type=0)]))
                return lambda: None
            async def bluetooth_device_connect(self, *args, **kwargs): return lambda: None
            async def bluetooth_device_pair(self, *args, **kwargs): return SimpleNamespace(paired=True)
            async def bluetooth_gatt_read(self, address, handle, **kwargs):
                return (660).to_bytes(2, 'little') + bytes(6) if handle == 1 else (300).to_bytes(2, 'little')
            async def bluetooth_device_disconnect(self, *args, **kwargs): pass
            async def disconnect(self): raise RuntimeError('disconnect transport failed')
        tunnel = SimpleNamespace(returncode=None, terminate=Mock(), kill=Mock(), wait=AsyncMock())
        writer = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        modules = {'aioesphomeapi': SimpleNamespace(APIClient=Client),
                   'aioesphomeapi.model': SimpleNamespace(LogLevel=SimpleNamespace(LOG_LEVEL_INFO=1))}
        with patch.dict(sys.modules, modules), \
             patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=tunnel)) as start, \
             patch.object(asyncio, 'open_connection', AsyncMock(return_value=(None, writer))):
            with self.assertRaises(RuntimeError):
                await monitor.read_sensor({'expected_address':123,'api_key':'private-placeholder'},
                                          {monitor.CURRENT:1,monitor.INTERVAL:2})
            tunnel.terminate.assert_called_once()
            self.assertEqual(start.call_args.args[-1], 'crosstown-mac')
            self.assertNotIn('private-placeholder', str(start.call_args))


if __name__ == '__main__':
    unittest.main()
