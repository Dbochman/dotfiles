#!/usr/bin/env python3
"""Fake-only security tests for the Cabin Google Assistant Roomba skill."""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
import importlib.util
from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOMBA_PATH = REPO_ROOT / "openclaw" / "skills" / "roomba" / "roomba"


def load_roomba(temp_home: Path):
    loader = SourceFileLoader(f"cabin_roomba_test_{id(temp_home)}", str(ROOMBA_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError(f"Could not load {ROOMBA_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"HOME": str(temp_home)}):
        loader.exec_module(module)
    return module


class CabinRoombaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.roomba = load_roomba(self.home)

    def test_credentials_are_written_atomically_and_owner_only(self) -> None:
        value = {"refresh_token": "fixture", "scopes": ["read-only-fixture"]}

        self.roomba.write_credentials(value)

        self.assertEqual(self.roomba.CONFIG_DIR.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.roomba.CREDS_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.roomba.CREDS_FILE.read_text()), value)
        self.assertEqual(list(self.roomba.CONFIG_DIR.glob(".credentials.*")), [])

    def test_loose_or_symlinked_credentials_are_rejected(self) -> None:
        self.roomba.CONFIG_DIR.mkdir(parents=True)
        self.roomba.CREDS_FILE.write_text("{}\n", encoding="utf-8")
        self.roomba.CREDS_FILE.chmod(0o644)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.roomba.ensure_credentials()

        self.roomba.CREDS_FILE.unlink()
        target = self.home / "outside.json"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o600)
        self.roomba.CREDS_FILE.symlink_to(target)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.roomba.ensure_credentials()


if __name__ == "__main__":
    unittest.main()
