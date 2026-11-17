"""Child-process fault injection, including Launch Services; never uses Bluetooth."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/airthings-monitor/scripts'
sys.path.insert(0, str(SCRIPTS))
import airthings
import airthings_launch as launch

# Use the actual executable: macOS framework Python re-execs its app binary.
TEST_PYTHON = subprocess.run(['/bin/ps', '-ww', '-p', str(os.getpid()), '-o', 'comm='], capture_output=True, text=True, check=True).stdout.strip()


class FakeSupervisor(launch.ReaderSupervisor):
    mode = 'success'

    def child_args(self, request):
        return [TEST_PYTHON, str(self.fixture), request['token'], str(request['deadline']),
                self.mode, str(self.result_path(request)), str(self.monitor.cache_file), str(self.payload_file)]

    def reader_command(self, request):
        return ' '.join(self.child_args(request))

    def launch_args(self, request):
        return [TEST_PYTHON, str(self.launcher), *self.child_args(request)]


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='airthings-watchdog-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.binding = {'alias': 'cabin-living-room-airthings', 'site': 'cabin',
                        'room': 'Living Room', 'model': 'Wave Enhance', 'address': 'PRIVATE-TEST-ID'}
        self.config.write_text(json.dumps({'schema_version': 1, 'devices': [self.binding]}))
        self.config.chmod(0o600)
        self.monitor = airthings.AirthingsMonitor(self.config, self.root / 'state')
        self.supervisor = FakeSupervisor(self.monitor, timeout=0.8)
        self.supervisor.fixture = self.root / 'reader.py'
        self.supervisor.launcher = self.root / 'launcher.py'
        self.supervisor.payload_file = self.root / 'payload.json'
        self.payload = airthings.offline_reading(self.binding, 'device_not_found')
        self.supervisor.payload_file.write_text(json.dumps(self.payload))
        self.supervisor.fixture.write_text('''import json, os, pathlib, signal, sys, time
_, token, deadline, mode, result, cache, payload_file = sys.argv
pathlib.Path(payload_file + '.pid').write_text(str(os.getpid()))
if mode == 'hang':
    os.kill(os.getpid(), signal.SIGSTOP)
    time.sleep(120)
if mode == 'ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.kill(os.getpid(), signal.SIGSTOP)
    time.sleep(120)
r = json.loads(pathlib.Path(payload_file).read_text())
if mode == 'success':
    r.update(online=True, battery_percent=71)
    r.pop('error', None)
    pathlib.Path(cache).write_text(json.dumps({'schema_version': 1, 'devices': {r['alias']: r}}))
    os.chmod(cache, 0o600)
if mode == 'private_error': r['error'] = 'PRIVATE-TEST-ID raw exception'
if mode == 'malformed':
    pathlib.Path(result).write_text('PRIVATE-TEST-ID raw exception')
else:
    pathlib.Path(result).write_text(json.dumps({'ok': True, 'devices': [r]}))
''')
        self.supervisor.launcher.write_text('''import subprocess, sys
p = subprocess.Popen(sys.argv[1:], start_new_session=True)
p.wait()
''')
        self.addCleanup(self.clean_children)
        self.requests = []

    def clean_children(self):
        for request in self.requests:
            with patch.object(launch, 'CLEANUP_GRACE', 0.2):
                self.supervisor.cleanup(request)
        if self.supervisor.active.exists():
            request = self.supervisor.previous_request()
            with patch.object(launch, 'CLEANUP_GRACE', 0.2):
                self.supervisor.cleanup(request)

    def read(self):
        with patch.object(launch, 'CLEANUP_GRACE', 0.2):
            return self.supervisor.status(self.binding['alias'], refresh=True)

    def assert_fixture_exited(self):
        pid = int(Path(str(self.supervisor.payload_file) + '.pid').read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail('Owned reader process survived watchdog cleanup')

    def test_fresh_success_and_private_health(self):
        result = self.read()
        self.assertTrue(result['devices'][0]['online'])
        self.assertEqual(result['devices'][0]['battery_percent'], 71)
        self.assertFalse(self.supervisor.active.exists())
        self.assertEqual(self.supervisor.health.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('PRIVATE-TEST-ID', json.dumps(result) + self.supervisor.health.read_text())

    def test_stopped_reader_times_out_is_reaped_and_next_attempt_works(self):
        self.supervisor.mode = 'hang'
        before = time.monotonic()
        result = self.read()
        self.assertLess(time.monotonic() - before, 4)
        self.assertEqual(result['devices'][0]['error'], 'reader_timeout')
        request = self.supervisor.previous_request()
        self.assertEqual(self.supervisor.matching_processes(request), [])
        self.assert_fixture_exited()
        self.supervisor.mode = 'success'
        self.assertTrue(self.read()['devices'][0]['online'])
        self.assertTrue(json.loads(self.supervisor.health.read_text())['recovered_previous_request'])

    def test_stopped_reader_ignoring_term_is_killed(self):
        self.supervisor.mode = 'ignore_term'
        self.assertEqual(self.read()['devices'][0]['error'], 'reader_timeout')
        self.assertEqual(self.supervisor.matching_processes(self.supervisor.previous_request()), [])
        self.assert_fixture_exited()

    def test_failed_refresh_preserves_cache_and_reports_actual_error(self):
        self.assertTrue(self.read()['devices'][0]['online'])
        before = self.monitor.cache_file.read_bytes()
        self.supervisor.mode = 'offline'
        result = self.read()
        self.assertFalse(result['devices'][0]['online'])
        self.assertEqual(result['devices'][0]['error'], 'device_not_found')
        self.assertEqual(before, self.monitor.cache_file.read_bytes())

    def test_cache_only_does_not_launch_or_cleanup(self):
        with patch.object(self.supervisor, 'launch_args', side_effect=AssertionError('launched')):
            result = self.supervisor.status(None, cache_only=True)
        self.assertEqual(result['devices'][0]['error'], 'cache_unavailable')

    def test_unknown_error_and_malformed_output_do_not_leak(self):
        for mode in ['private_error', 'malformed']:
            self.supervisor.mode = mode
            result = self.read()
            self.assertEqual(result['devices'][0]['error'], 'reader_failed')
            self.assertNotIn('PRIVATE-TEST-ID', json.dumps(result) + self.supervisor.health.read_text())

    def test_late_old_output_cannot_replace_new_request_result(self):
        self.assertTrue(self.read()['devices'][0]['online'])
        old = {'token': 'a' * 32, 'alias': None, 'deadline': 1}
        current = {'token': 'b' * 32, 'alias': None, 'deadline': 2}
        device = self.monitor._fresh_cached(self.binding['alias'])
        current_result = self.supervisor.result_path(current)
        current_result.write_text(json.dumps({'ok': True, 'devices': [device]}))
        current_result.chmod(0o600)
        old_result = self.supervisor.result_path(old)
        old_result.write_text('late old reader output')
        old_result.chmod(0o600)
        result = self.supervisor.fresh_payload(current, [self.binding])
        self.assertTrue(result['devices'][0]['online'])
        self.supervisor.remove_result(old)
        self.assertTrue(current_result.exists())

    def test_concurrent_request_does_not_launch(self):
        airthings._private_dir(self.monitor.state_dir)
        with (self.monitor.state_dir / 'reader.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(self.supervisor, 'launch_args', side_effect=AssertionError('launched')):
                result = self.supervisor.status(None, refresh=True)
        self.assertEqual(result['devices'][0]['error'], 'reader_busy')

    def reap_fixture(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)

    def spawn_stopped(self, token):
        request = {'token': token * 32, 'alias': self.binding['alias'], 'deadline': int(time.time()) - 1}
        self.requests.append(request)
        child = subprocess.Popen(self.supervisor.child_args(request), start_new_session=True)
        self.addCleanup(self.reap_fixture, child)
        for _ in range(100):
            state = subprocess.run(['/bin/ps', '-p', str(child.pid), '-o', 'stat='], capture_output=True, text=True).stdout
            if state.strip().startswith('T'):
                break
            time.sleep(0.01)
        else:
            self.fail('fixture never stopped')
        return request, child

    def test_previous_orphan_is_cleaned_without_touching_unrelated_reader(self):
        self.supervisor.mode = 'hang'
        airthings._private_dir(self.monitor.state_dir)
        old, owned = self.spawn_stopped('a')
        other, unrelated = self.spawn_stopped('b')
        airthings._atomic_json(self.supervisor.active, old)
        # Keep this fixture's command stable while changing its run behavior.
        with patch.object(self.supervisor, 'refresh', return_value={'ok': True, 'devices': []}):
            self.supervisor.status(None, refresh=True)
        self.assertIsNotNone(owned.poll())
        self.assertIsNone(unrelated.poll())
        with patch.object(launch, 'CLEANUP_GRACE', 0.2):
            self.supervisor.cleanup(other)

    def test_process_identity_is_rechecked_before_signal(self):
        request = {'token': 'c' * 32, 'alias': None, 'deadline': 0}
        with patch.object(self.supervisor, 'matching_processes', return_value=[]), patch.object(os, 'kill') as kill:
            self.supervisor.signal_owned(request, 12345, signal.SIGTERM)
        kill.assert_not_called()

    def test_unsafe_journal_and_result_fail_closed(self):
        airthings._private_dir(self.monitor.state_dir)
        target = self.root / 'untouched'
        target.write_text('original')
        self.supervisor.active.symlink_to(target)
        with self.assertRaises(airthings.AirthingsError):
            self.read()
        self.supervisor.active.unlink()
        from types import SimpleNamespace
        token = 'e' * 32
        self.supervisor.result_path({'token': token}).symlink_to(target)
        with patch.object(launch.uuid, 'uuid4', return_value=SimpleNamespace(hex=token)):
            with self.assertRaises(FileExistsError):
                self.read()
        self.assertEqual(target.read_text(), 'original')

    @unittest.skipUnless(sys.platform == 'darwin' and (Path.home() / '.openclaw/apps/Airthings Reader.app').is_dir(),
                         'requires existing authorized reader app; no Bluetooth used')
    def test_real_launch_services_stopped_reader_is_cleaned(self):
        # Exercise the real detached app boundary with a harmless fixture that
        # never imports BLE. Only this uniquely tagged request may be signaled.
        actual = launch.ReaderSupervisor(self.monitor, timeout=3)
        actual.script = self.root / 'stopped fixture.py'
        pid_file = self.root / 'actual.pid'
        actual.script.write_text('import os,signal\nfrom pathlib import Path\n'
                                 + f'Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
                                 + 'os.kill(os.getpid(),signal.SIGSTOP)\n')
        result = actual.status(None, refresh=True)
        self.assertEqual(result['devices'][0]['error'], 'reader_timeout')
        self.assertTrue(pid_file.exists(), 'Launch Services never started the fixture')
        pid = int(pid_file.read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail('Detached app survived watchdog cleanup')

    def test_delayed_reader_exits_without_bluetooth(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'airthings.py'), 'status', '--refresh', '--json',
                                 '--reader-request', 'd' * 32, '--reader-deadline', '1'],
                                env={**os.environ, 'AIRTHINGS_CONFIG_FILE': str(self.config),
                                     'AIRTHINGS_STATE_DIR': str(self.monitor.state_dir)},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['devices'][0]['error'], 'reader_timeout')
        self.assertFalse(self.monitor.cache_file.exists())


if __name__ == '__main__':
    unittest.main()
