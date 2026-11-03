import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'openclaw/bin/presence-crosstown-run.py'
SPEC = importlib.util.spec_from_file_location('presence_crosstown_run', HELPER)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class CrosstownRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assert_stopped(self, pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                    capture_output=True, text=True)
            if result.returncode != 0 or result.stdout.strip().startswith('Z'):
                return
            time.sleep(0.02)
        self.fail('scanner descendant survived cleanup')

    def hanging_scanner(self, path):
        child = self.root / 'stuck.py'
        pidfile = self.root / 'child.pid'
        child.write_text('import os, signal, time\nfrom pathlib import Path\n'
                         'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                         f'Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
                         'while True: time.sleep(1)\n')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/bash\n"{sys.executable}" "{child}" &\nwait\n')
        return pidfile

    def test_deadline_kills_stuck_descendant_and_next_run_succeeds(self):
        scanner = self.root / 'scanner.sh'
        pidfile = self.hanging_scanner(scanner)
        state = self.root / 'state.json'
        state.write_text('{"previous":"preserved"}')
        before = state.read_bytes()
        start = time.monotonic()
        self.assertEqual(RUNNER.run_bounded(['/bin/bash', str(scanner)],
                                          timeout_seconds=0.4, grace_seconds=0.1),
                         (124, 'deadline_exceeded'))
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(pidfile.is_file())
        self.assert_stopped(int(pidfile.read_text()))
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(RUNNER.run_bounded(['/bin/bash', '-c', 'exit 0']), (0, 'completed'))

    def test_parent_and_child_ignoring_term_are_killed(self):
        scanner = self.root / 'scanner.sh'
        pidfile = self.hanging_scanner(scanner)
        scanner.write_text(scanner.read_text().replace('#!/bin/bash\n', '#!/bin/bash\ntrap "" TERM\n'))
        self.assertEqual(RUNNER.run_bounded(['/bin/bash', str(scanner)],
                                          timeout_seconds=0.4, grace_seconds=0.1),
                         (124, 'deadline_exceeded'))
        self.assert_stopped(int(pidfile.read_text()))

    def test_failure_exit_and_output_privacy(self):
        scanner = self.root / '.openclaw/workspace/scripts/presence-detect.sh'
        scanner.parent.mkdir(parents=True)
        scanner.write_text('echo PRIVATE_NETWORK_DATA\necho PRIVATE_NETWORK_DATA >&2\nexit 7\n')
        result = subprocess.run([sys.executable, str(HELPER)],
                                env={**os.environ, 'HOME': str(self.root)},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('PRIVATE_NETWORK_DATA', result.stderr)
        self.assertEqual(json.loads(result.stderr)['outcome'], 'scanner_failed')

    def test_shutdown_cleans_up_descendants(self):
        scanner = self.root / '.openclaw/workspace/scripts/presence-detect.sh'
        pidfile = self.hanging_scanner(scanner)
        process = subprocess.Popen([sys.executable, str(HELPER)],
                                   env={**os.environ, 'HOME': str(self.root)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists())
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 128 + signal.SIGTERM)
            self.assertEqual(stdout, '')
            self.assertEqual(json.loads(stderr)['outcome'], 'interrupted')
            self.assert_stopped(int(pidfile.read_text()))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_launchagent_uses_bounded_runner_before_next_interval(self):
        import plistlib
        plist = plistlib.loads((ROOT / 'openclaw/launchagents/com.openclaw.presence-crosstown.plist').read_bytes())
        self.assertEqual(plist['ProgramArguments'], ['/usr/bin/python3',
                         '/Users/dbochman/.openclaw/bin/presence-crosstown-run.py'])
        self.assertLess(RUNNER.TIMEOUT_SECONDS + RUNNER.TERM_GRACE_SECONDS + 1, plist['StartInterval'])
        self.assertLess(RUNNER.TERM_GRACE_SECONDS + 1, plist['ExitTimeOut'])


if __name__ == '__main__':
    unittest.main()
