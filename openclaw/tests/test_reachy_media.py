#!/usr/bin/env python3

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reachy-control"
    / "scripts"
    / "reachy_media.py"
)
SPEC = importlib.util.spec_from_file_location("reachy_media", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
reachy_media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reachy_media)


class ReachyMediaTests(unittest.TestCase):
    def test_capture_creates_private_jpeg_and_cleanup_removes_it(self) -> None:
        jpeg = b"\xff\xd8camera-frame\xff\xd9"

        def stream(command, **kwargs):
            kwargs["stdout"].write(jpeg)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with tempfile.TemporaryDirectory() as temporary:
            media_root = Path(temporary) / "media"
            with (
                patch.dict(os.environ, {"REACHY_MEDIA_DIR": str(media_root)}),
                patch.object(reachy_media, "_stream_command", return_value=["capture"]),
                patch.object(reachy_media.subprocess, "run", side_effect=stream),
            ):
                result = reachy_media.capture()
                path = Path(result["mediaPath"])

                self.assertEqual(path.read_bytes(), jpeg)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(media_root.stat().st_mode), 0o700)

                cleanup = reachy_media.cleanup(result["cleanupToken"])

            self.assertEqual(cleanup, {"status": "success", "cleaned": True})
            self.assertFalse(path.exists())

    def test_invalid_capture_is_removed(self) -> None:
        def stream(command, **kwargs):
            kwargs["stdout"].write(b"not-a-jpeg")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with tempfile.TemporaryDirectory() as temporary:
            media_root = Path(temporary) / "media"
            with (
                patch.dict(os.environ, {"REACHY_MEDIA_DIR": str(media_root)}),
                patch.object(reachy_media, "_stream_command", return_value=["capture"]),
                patch.object(reachy_media.subprocess, "run", side_effect=stream),
            ):
                with self.assertRaisesRegex(
                    reachy_media.MediaError,
                    "invalid JPEG",
                ):
                    reachy_media.capture()

            self.assertEqual(list(media_root.iterdir()), [])

    def test_cleanup_rejects_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            media_root = Path(temporary) / "media"
            media_root.mkdir(mode=0o700)
            target = Path(temporary) / "outside.jpg"
            target.write_bytes(b"keep")
            token = "reachy-abcdef12.jpg"
            (media_root / token).symlink_to(target)

            with patch.dict(os.environ, {"REACHY_MEDIA_DIR": str(media_root)}):
                with self.assertRaisesRegex(
                    reachy_media.MediaError,
                    "cleanup target is invalid",
                ):
                    reachy_media.cleanup(token)

            self.assertEqual(target.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
