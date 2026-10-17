"""Exercise the macOS reader packaging without requesting Bluetooth access."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "skills/airthings-monitor/scripts/install_macos_reader.sh"
SOURCE_APP = Path("/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Resources/Python.app")


@unittest.skipUnless(sys.platform == "darwin" and SOURCE_APP.is_dir(), "requires Homebrew macOS Python")
class AirthingsReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="airthings-reader-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime_home = self.root / "runtime"
        self.runtime_home.mkdir()
        self.app = self.runtime_home / "apps/Airthings Reader.app"

    def install(self) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "AIRTHINGS_SOURCE_PYTHON_APP": str(SOURCE_APP)}
        return subprocess.run(["/bin/bash", str(INSTALLER), str(self.runtime_home)], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_signed_bundle_is_preserved_on_repeat_install(self) -> None:
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        executable = self.app / "Contents/MacOS/Airthings Reader"
        before = executable.stat()
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("unchanged", second.stdout)
        self.assertEqual((before.st_ino, before.st_mtime_ns),
                         (executable.stat().st_ino, executable.stat().st_mtime_ns))
        verified = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(self.app)],
                                  capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_rebuild_retains_previous_bundle(self) -> None:
        self.assertEqual(self.install().returncode, 0)
        before = (self.app / "Contents/MacOS/Airthings Reader").read_bytes()
        (self.app.parent / ".airthings-reader-inputs").write_text("old inputs\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(self.app.parent.glob(".airthings-reader-build.*/previous.app"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "Contents/MacOS/Airthings Reader").read_bytes(), before)

    def test_symlinked_app_is_not_overwritten(self) -> None:
        self.app.parent.mkdir()
        target = self.root / "untouched"
        target.mkdir()
        self.app.symlink_to(target, target_is_directory=True)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertTrue(self.app.is_symlink())
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
