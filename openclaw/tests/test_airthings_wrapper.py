"""The public CLI must supervise status reads and fail closed if that helper is missing."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WRAPPER = Path(__file__).resolve().parents[1] / 'bin/airthings'


class AirthingsWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reader = self.root / 'airthings.py'
        self.supervisor = self.root / 'airthings_launch.py'
        self.reader.touch()
        self.supervisor.touch()
        self.runtime = self.root / 'python'
        self.runtime.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        self.runtime.chmod(0o755)

    def run_wrapper(self, *args):
        return subprocess.run([str(WRAPPER), *args], capture_output=True, text=True,
                              env={**os.environ, 'AIRTHINGS_SCRIPT': str(self.reader),
                                   'AIRTHINGS_RUNTIME': str(self.runtime),
                                   'AIRTHINGS_LAUNCH_SCRIPT': str(self.supervisor)}, timeout=5)

    def test_status_and_cache_only_go_through_supervisor(self):
        for flags in [[], ['--refresh'], ['--cache-only']]:
            result = self.run_wrapper('status', *flags, '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(self.supervisor), 'status', *flags, '--json'])

    def test_devices_keep_existing_reader(self):
        result = self.run_wrapper('devices', '--json')
        self.assertEqual(result.stdout.splitlines(), [str(self.reader), 'devices', '--json'])

    def test_missing_supervisor_does_not_fall_back_to_unsupervised_bluetooth(self):
        self.supervisor.unlink()
        result = self.run_wrapper('status', '--refresh', '--json')
        self.assertEqual(result.returncode, 12)
        self.assertIn('supervisor_unavailable', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_symlinked_supervisor_is_rejected(self):
        self.supervisor.unlink()
        self.supervisor.symlink_to(self.reader)
        self.assertEqual(self.run_wrapper('status', '--json').returncode, 12)


if __name__ == '__main__':
    unittest.main()
