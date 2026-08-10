#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "openclaw" / "bin" / "airthings"


class AirthingsWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.refreshed = self.root / "refreshed"
        self.open_args = self.root / "open-args"
        self.site_packages = self.root / "site-packages"
        self.site_packages.mkdir()
        self.python_app = self.root / "Python.app"
        self.python_app.mkdir()
        self.script = self.root / "airthings.py"
        self.script.write_text("# test fixture\n", encoding="utf-8")
        self.runtime = self.root / "python"
        self.runtime.write_text(
            """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "-c" ]]; then
  printf '%s\\n' "$FAKE_SITE_PACKAGES"
elif [[ -f "$FAKE_REFRESHED" ]]; then
  printf '%s\\n' '{"devices":[{"online":true,"cached":true}],"ok":true}'
else
  printf '%s\\n' '{"devices":[{"online":false,"error":"bluetooth_unauthorized"}],"ok":true}'
fi
""",
            encoding="utf-8",
        )
        self.runtime.chmod(0o755)
        self.open_bin = self.root / "open"
        self.open_bin.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" > "$FAKE_OPEN_ARGS"
: > "$FAKE_REFRESHED"
""",
            encoding="utf-8",
        )
        self.open_bin.chmod(0o755)

    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "AIRTHINGS_RUNTIME": str(self.runtime),
                "AIRTHINGS_SCRIPT": str(self.script),
                "AIRTHINGS_PYTHON_APP": str(self.python_app),
                "AIRTHINGS_OPEN_BIN": str(self.open_bin),
                "FAKE_SITE_PACKAGES": str(self.site_packages),
                "FAKE_REFRESHED": str(self.refreshed),
                "FAKE_OPEN_ARGS": str(self.open_args),
            }
        )
        return subprocess.run(
            [str(WRAPPER), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_stale_status_refreshes_through_authorized_python_app(self) -> None:
        result = self.run_wrapper(
            "status", "cabin-living-room-airthings", "--json"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"online":true', result.stdout)
        launch_args = self.open_args.read_text(encoding="utf-8")
        self.assertIn("-g -W -n", launch_args)
        self.assertIn(str(self.python_app), launch_args)
        self.assertIn("--refresh --json", launch_args)

    def test_fresh_status_does_not_launch_python_app(self) -> None:
        self.refreshed.touch()

        result = self.run_wrapper("status", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"online":true', result.stdout)
        self.assertFalse(self.open_args.exists())

    def test_explicit_refresh_always_uses_authorized_python_app(self) -> None:
        self.refreshed.touch()

        result = self.run_wrapper("status", "--refresh", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.open_args.exists())


if __name__ == "__main__":
    unittest.main()
