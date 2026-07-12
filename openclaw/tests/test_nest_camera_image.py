#!/usr/bin/env python3
"""Focused offline tests for short-lived OpenClaw Nest camera images."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "openclaw" / "bin" / "nest-camera-image"
SKILL = REPO_ROOT / "openclaw" / "skills" / "nest-camera" / "SKILL.md"
THERMOSTAT_SKILL = (
    REPO_ROOT / "openclaw" / "skills" / "nest-thermostat" / "SKILL.md"
)
JPEG = bytes.fromhex(
    "ffd8"
    "ffe000104a46494600010100000100010000"
    "ffc0000b080001000101011100"
    "ffda0008010100003f00"
    "00"
    "ffd9"
)


def load_helper():
    loader = importlib.machinery.SourceFileLoader(
        "nest_camera_image_for_test", str(HELPER)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class NestCameraImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()

    def make_fake_nest(
        self,
        root: Path,
        *,
        payload: bytes = JPEG,
        output_mode: int = 0o600,
        stale: bool = False,
        oversized: bool = False,
    ) -> Path:
        fake = root / "fake-nest"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            f"payload = bytes.fromhex({payload.hex()!r})\n"
            f"oversized = {oversized!r}\n"
            "pathlib.Path(__file__).with_suffix('.log').write_text("
            "json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "destination = pathlib.Path(sys.argv[-1])\n"
            "if oversized:\n"
            "    payload += b'x' * (16 * 1024 * 1024 + 1)\n"
            "destination.write_bytes(payload)\n"
            f"destination.chmod({output_mode:#o})\n"
            f"if {stale!r}:\n"
            "    os.utime(destination, (1, 1))\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake

    def test_capture_uses_exact_alias_and_returns_only_safe_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake = self.make_fake_nest(root)
            media = root / "media"
            reaper_calls: list[tuple[str, Path]] = []

            for alias in ("Kitchen", "Living Room", "Living Room Wired"):
                with self.subTest(alias=alias):
                    result = self.helper.capture_image(
                        alias,
                        media_directory=media,
                        nest_binary=fake,
                        reaper=lambda token, path: reaper_calls.append(
                            (token, path)
                        ),
                    )
                    self.assertEqual(
                        set(result), {"alias", "mediaPath", "cleanupToken"}
                    )
                    self.assertEqual(result["alias"], alias)
                    token = result["cleanupToken"]
                    self.assertRegex(token, r"^[0-9a-f]{48}$")
                    image = Path(result["mediaPath"])
                    self.assertEqual(image, media / f"{token}.jpg")
                    self.assertEqual(image.read_bytes(), JPEG)
                    self.assertEqual(stat.S_IMODE(image.stat().st_mode), 0o600)
                    self.assertEqual(
                        stat.S_IMODE(media.stat().st_mode), 0o700
                    )
                    argv = json.loads(fake.with_suffix(".log").read_text())
                    self.assertEqual(
                        argv,
                        ["camera", "snap-config", alias, str(image)],
                    )
                    self.assertEqual(reaper_calls[-1], (token, media))
                    self.helper.cleanup_image(token, media_directory=media)
                    self.assertFalse(image.exists())

    def test_unknown_or_noncanonical_alias_never_invokes_nest(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake = self.make_fake_nest(root)
            for alias in ("kitchen", "Cabin Kitchen", "Laundry", ""):
                with self.subTest(alias=alias):
                    with self.assertRaisesRegex(
                        self.helper.PublicError, "^Unknown camera alias$"
                    ):
                        self.helper.capture_image(
                            alias,
                            media_directory=root / "media",
                            nest_binary=fake,
                            reaper=lambda _token, _path: None,
                        )
            self.assertFalse(fake.with_suffix(".log").exists())

    def test_media_directory_must_be_real_owner_only_and_mode_0700(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake = self.make_fake_nest(root)
            permissive = root / "permissive"
            permissive.mkdir(mode=0o755)
            permissive.chmod(0o755)
            real = root / "real"
            real.mkdir(mode=0o700)
            link = root / "linked"
            link.symlink_to(real, target_is_directory=True)
            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o700)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            for media in (permissive, link, linked_parent / "media"):
                with self.subTest(media=media):
                    with self.assertRaisesRegex(
                        self.helper.PublicError,
                        "^Camera image directory is unavailable$",
                    ):
                        self.helper.capture_image(
                            "Kitchen",
                            media_directory=media,
                            nest_binary=fake,
                            reaper=lambda _token, _path: None,
                        )

    def test_invalid_capture_is_deleted_and_error_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            fake = self.make_fake_nest(root, payload=b"private-device-id")
            media = root / "media"
            with self.assertRaisesRegex(
                self.helper.PublicError, "^Captured camera image is invalid$"
            ) as context:
                self.helper.capture_image(
                    "Kitchen",
                    media_directory=media,
                    nest_binary=fake,
                    reaper=lambda _token, _path: None,
                )
            self.assertNotIn("private-device-id", str(context.exception))
            self.assertEqual(list(media.iterdir()), [])

    def test_capture_rejects_stale_permissive_and_oversized_files(self) -> None:
        scenarios = (
            {"stale": True},
            {"output_mode": 0o644},
            {"oversized": True},
        )
        for options in scenarios:
            with self.subTest(options=options):
                with tempfile.TemporaryDirectory() as tempdir:
                    root = Path(tempdir)
                    fake = self.make_fake_nest(root, **options)
                    media = root / "media"
                    with self.assertRaisesRegex(
                        self.helper.PublicError,
                        "^Captured camera image is invalid$",
                    ):
                        self.helper.capture_image(
                            "Kitchen",
                            media_directory=media,
                            nest_binary=fake,
                            reaper=lambda _token, _path: None,
                        )
                    self.assertEqual(list(media.iterdir()), [])

    def test_cleanup_accepts_only_opaque_tokens_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            media = Path(tempdir) / "media"
            media.mkdir(mode=0o700)
            token = "a" * 48
            image = media / f"{token}.jpg"
            image.write_bytes(JPEG)
            image.chmod(0o600)
            protected = Path(tempdir) / "protected.jpg"
            protected.write_bytes(b"keep")

            for invalid in ("../protected", str(protected), token + ".jpg", "A" * 48):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(
                        self.helper.PublicError,
                        "^Invalid camera image cleanup token$",
                    ):
                        self.helper.cleanup_image(
                            invalid, media_directory=media
                        )
            self.assertEqual(protected.read_bytes(), b"keep")
            self.helper.cleanup_image(token, media_directory=media)
            self.helper.cleanup_image(token, media_directory=media)
            self.assertFalse(image.exists())

    def test_sweep_removes_only_old_owned_filename_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            media = Path(tempdir) / "media"
            media.mkdir(mode=0o700)
            now = time.time()
            old_image = media / (("b" * 48) + ".jpg")
            old_temp = media / ("." + ("c" * 48) + ".jpg.partial.tmp")
            fresh_image = media / (("d" * 48) + ".jpg")
            unrelated = media / "notes.txt"
            for path in (old_image, old_temp, fresh_image, unrelated):
                path.write_bytes(b"data")
                path.chmod(0o600)
            os.utime(old_image, (now - 1000, now - 1000))
            os.utime(old_temp, (now - 1000, now - 1000))

            removed = self.helper.sweep_images(
                media_directory=media, now=now, ttl_seconds=900
            )

            self.assertEqual(removed, 2)
            self.assertFalse(old_image.exists())
            self.assertFalse(old_temp.exists())
            self.assertTrue(fresh_image.exists())
            self.assertTrue(unrelated.exists())

    def test_reaper_is_detached_sanitized_and_uses_fixed_default_ttl(self) -> None:
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return object()

        token = "e" * 48
        media = self.helper._media_directory()
        self.helper.spawn_cleanup_reaper(
            token, media, popen_factory=fake_popen
        )

        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[0:3], ["/bin/sh", "-c", argv[2]])
        self.assertIn(str(self.helper.DEFAULT_TTL_SECONDS), argv)
        self.assertIn("cleanup", argv[2])
        self.assertIn(token, argv)
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["cwd"], "/")
        self.assertEqual(
            set(kwargs["env"]),
            {"HOME", "PATH"},
        )

    def test_cli_capture_json_has_exact_contract(self) -> None:
        result = {
            "alias": "Kitchen",
            "mediaPath": "/safe/image.jpg",
            "cleanupToken": "f" * 48,
        }
        stdout = io.StringIO()
        with mock.patch.object(self.helper, "capture_image", return_value=result):
            with mock.patch("sys.stdout", stdout):
                status = self.helper.main(["capture", "Kitchen"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(
            set(json.loads(stdout.getvalue())),
            {"alias", "mediaPath", "cleanupToken"},
        )

    def test_skill_requires_private_source_routed_delivery_and_cleanup(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        for required in (
            "one-to-one conversation",
            "Refuse group",
            "explicit channel and target fields",
            'message(action="send"',
            "nest-camera-image cleanup '<cleanupToken>'",
            "Treat cleanup as a `finally` step",
            "return only `NO_REPLY`",
            "monitoring frames are deliberately not retained",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertIn(
            "allowed-tools: Bash(nest-camera-image:*), message", text
        )

    def test_camera_requests_have_one_unambiguous_skill_owner(self) -> None:
        camera_text = SKILL.read_text(encoding="utf-8")
        thermostat_text = THERMOSTAT_SKILL.read_text(encoding="utf-8")
        for alias in ("`Kitchen`", "`Living Room`", "`Living Room Wired`"):
            self.assertIn(alias, camera_text)
        thermostat_description = thermostat_text.split("---", 2)[1]
        self.assertNotIn("camera", thermostat_description.casefold())


if __name__ == "__main__":
    unittest.main()
