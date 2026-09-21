"""Offline regression checks. Run with the same NumPy/Pillow environment as the helpers."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import numpy as np
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HelperTests(unittest.TestCase):
    def test_selector_preserves_existing_output_and_handles_empty_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, destination = root / "raw", root / "images"
            source.mkdir()
            destination.mkdir()
            sentinel = destination / "notes.txt"
            sentinel.write_text("preserve")
            run = subprocess.run([sys.executable, str(SCRIPTS / "select_sharp.py"), str(source), str(destination)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(sentinel.read_text(), "preserve")
            fresh = root / "fresh"
            run = subprocess.run([sys.executable, str(SCRIPTS / "select_sharp.py"), str(source), str(fresh)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(fresh.exists())

    def test_selector_keeps_sharper_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw"
            source.mkdir()
            Image.new("L", (64, 64), 128).save(source / "01.jpg")
            noise = np.random.default_rng(42).integers(0, 256, (64, 64), dtype=np.uint8)
            Image.fromarray(noise).save(source / "02.jpg")
            output = root / "images"
            subprocess.run([sys.executable, str(SCRIPTS / "select_sharp.py"), str(source), str(output)], check=True, capture_output=True)
            self.assertEqual((output / "frame_0001.jpg").read_bytes(), (source / "02.jpg").read_bytes())
            self.assertEqual(len(list(source.glob("*.jpg"))), 2)

    def test_upload_errors_redact_url_and_body(self):
        publisher = load("publish_to_supersplat")
        url = "https://example.invalid/upload?signature=SECRET"
        errors = [urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"SECRET")),
                  urllib.error.URLError(url)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(publisher.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(SystemExit) as result:
                    publisher.call("PUT", url, raw=b"fixture")
                self.assertNotIn("SECRET", str(result.exception))
                self.assertNotIn(url, str(result.exception))

    def test_preview_rejects_truncated_ply_and_preserves_landscape_ratio(self):
        preview = load("point_preview")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ply = root / "scene.ply"
            ply.write_bytes(b"ply\nformat binary_little_endian 1.0\n")
            with self.assertRaises(ValueError):
                preview.read_ply(ply)
            names = ("x", "y", "z", "opacity", "f_dc_0", "f_dc_1", "f_dc_2")
            data = np.zeros(1, dtype=[(name, "<f4") for name in names])
            data["z"], data["opacity"] = 1, 1
            header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
            header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
            ply.write_bytes(header.encode() + data.tobytes())
            (root / "cameras.txt").write_text("1 PINHOLE 800 400 400 400 400 200\n")
            (root / "images.txt").write_text("1 1 0 0 0 0 0 0 1 frame.jpg\n\n")
            output = root / "preview"
            with patch.object(sys, "argv", ["preview", str(root), "frame.jpg", str(output), str(ply), "--opacity-format", "linear"]), contextlib.redirect_stdout(io.StringIO()):
                preview.main()
            with Image.open(output / "points_frame_noflip.png") as image:
                self.assertEqual(image.size, (800, 400))
                self.assertGreater(sum(image.getpixel((400, 200))), 0)
            ply.write_bytes(header.encode() + b"short")
            with self.assertRaises(ValueError):
                preview.read_ply(ply)

    def test_shell_scripts_refuse_existing_outputs_outside_skill_directory(self):
        with tempfile.TemporaryDirectory(prefix="splat project ") as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            (root / "colmap/undistorted/images").mkdir(parents=True)
            (root / "colmap/undistorted/sparse/0").mkdir(parents=True)
            (root / "output").mkdir()
            sentinel = root / "output/keep.txt"
            sentinel.write_text("preserve")
            for name, expected in (("run_colmap.sh", "colmap already exists"), ("train_brush_fast.sh", "output exists")):
                result = subprocess.run(["bash", str(SCRIPTS / name), str(root)], cwd="/tmp", capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
            self.assertEqual(sentinel.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
