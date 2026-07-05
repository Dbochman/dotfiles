#!/usr/bin/env python3
"""Fake-UI safety tests for findmy-locate.sh."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "openclaw" / "skills" / "findmy-locate" / "findmy-locate.sh"


class FindMyLocateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.capture_dir = self.root / "captures"
        self.log = self.root / "calls.log"
        self.selection = self.root / "selected.txt"

        self.open_bin = self.fake_bin / "open"
        self.peekaboo_bin = self.fake_bin / "peekaboo"
        self.nohup_bin = self.fake_bin / "nohup"
        self.sleep_bin = self.fake_bin / "sleep"

        self._write_executable(
            self.open_bin,
            r'''#!/usr/bin/env bash
set -euo pipefail
printf 'open:%s\n' "$*" >> "$FAKE_LOG"
[[ "${FAKE_MODE:-}" != "open-fail" ]]
''',
        )
        self._write_executable(
            self.sleep_bin,
            "#!/usr/bin/env bash\nexit 0\n",
        )
        self._write_executable(
            self.nohup_bin,
            r'''#!/usr/bin/env bash
set -euo pipefail
printf 'nohup:%s\n' "$*" >> "$FAKE_LOG"
exit 0
''',
        )
        self._write_executable(
            self.peekaboo_bin,
            r'''#!/usr/bin/env bash
set -euo pipefail
command="${1:-}"
shift || true

case "$command" in
  see)
    printf 'peekaboo:see\n' >> "$FAKE_LOG"
    selected=""
    [[ -f "$FAKE_SELECTION" ]] && selected=$(cat "$FAKE_SELECTION")
    people_selected=true
    dylan_name="Dylan Bochman"
    dylan_selected=false
    julia_selected=false
    me_selected=false
    [[ "$selected" == "D1" ]] && dylan_selected=true
    [[ "$selected" == "J2" ]] && julia_selected=true
    [[ "$selected" == "M0" ]] && me_selected=true
    case "${FAKE_MODE:-}" in
      people-tab-mismatch)
        people_selected=false
        ;;
      name-mismatch)
        dylan_name="Dylan B."
        ;;
      selection-mismatch)
        if [[ -n "$selected" ]]; then
          dylan_selected=false
          julia_selected=true
        fi
        ;;
    esac
    printf '{"data":{"elements":[{"id":"TAB","role":"AXRadioButton","label":"People","selected":%s},{"id":"J2","role":"AXRow","name":"Julia Jennings","selected":%s},{"id":"M0","role":"AXRow","name":"Me","selected":%s},{"id":"D1","role":"AXRow","name":"%s","selected":%s}]}}\n' \
      "$people_selected" "$julia_selected" "$me_selected" "$dylan_name" "$dylan_selected"
    ;;
  click)
    element=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --on)
          element="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done
    printf 'peekaboo:click:%s\n' "$element" >> "$FAKE_LOG"
    [[ "${FAKE_MODE:-}" != "click-fail" ]] || exit 9
    printf '%s' "$element" > "$FAKE_SELECTION"
    ;;
  image)
    path=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --path)
          path="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done
    selected=""
    [[ -f "$FAKE_SELECTION" ]] && selected=$(cat "$FAKE_SELECTION")
    printf 'peekaboo:image:%s\n' "$selected" >> "$FAKE_LOG"
    if [[ "${FAKE_MODE:-}" == "julia-capture-fail" && "$selected" == "J2" ]]; then
      exit 9
    fi
    head -c 4096 /dev/zero > "$path"
    ;;
  *)
    exit 8
    ;;
esac
''',
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_script(
        self,
        *args: str,
        fake_mode: str = "",
        nohup_bin: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.root / "home"),
                "FAKE_LOG": str(self.log),
                "FAKE_MODE": fake_mode,
                "FAKE_SELECTION": str(self.selection),
                "FINDMY_CAPTURE_DIR": str(self.capture_dir),
                "FINDMY_CAPTURE_TTL_SECONDS": "60",
                "FINDMY_MIN_CAPTURE_BYTES": "1024",
                "FINDMY_OPEN_BIN": str(self.open_bin),
                "FINDMY_PEEKABOO_BIN": str(self.peekaboo_bin),
                "FINDMY_NOHUP_BIN": str(nohup_bin or self.nohup_bin),
                "FINDMY_SLEEP_BIN": str(self.sleep_bin),
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    @staticmethod
    def parse_output(result: subprocess.CompletedProcess[str]) -> dict:
        lines = result.stdout.splitlines()
        if len(lines) != 1:
            raise AssertionError(f"Expected one JSON line, got {result.stdout!r}")
        return json.loads(lines[0])

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_reordered_sidebar_uses_exact_name_and_verifies_selection(self) -> None:
        result = self.run_script("dylan")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.parse_output(result)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["person"], "Dylan Bochman")
        self.assertIn("peekaboo:click:D1", self.calls())
        self.assertIn("peekaboo:image:D1", self.calls())
        self.assertEqual(self.calls().count("peekaboo:see"), 2)

        capture = Path(payload["capture"])
        self.assertTrue(capture.is_file())
        self.assertEqual(stat.S_IMODE(capture.stat().st_mode), 0o600)
        self.assertEqual(payload["delete_after_seconds"], 60)
        self.assertTrue(any(call.startswith("nohup:") for call in self.calls()))

    def test_people_tab_and_exact_name_mismatches_fail_before_click(self) -> None:
        for mode in ("people-tab-mismatch", "name-mismatch"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                self.selection.unlink(missing_ok=True)
                result = self.run_script("dylan", fake_mode=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.parse_output(result)["error"], "person_not_verified")
                self.assertFalse(any("peekaboo:click" in call for call in self.calls()))
                self.assertFalse(any("peekaboo:image" in call for call in self.calls()))

    def test_selected_person_is_reverified_before_capture(self) -> None:
        result = self.run_script("dylan", fake_mode="selection-mismatch")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.parse_output(result)["error"], "selection_mismatch")
        self.assertIn("peekaboo:click:D1", self.calls())
        self.assertFalse(any("peekaboo:image" in call for call in self.calls()))

    def test_both_captures_exact_people_and_succeeds(self) -> None:
        result = self.run_script("both")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.parse_output(result)
        self.assertEqual(len(payload["results"]), 2)
        self.assertTrue(all(item["success"] for item in payload["results"]))
        self.assertIn("peekaboo:click:D1", self.calls())
        self.assertIn("peekaboo:click:J2", self.calls())

    def test_both_exits_nonzero_if_either_capture_fails(self) -> None:
        result = self.run_script("both", fake_mode="julia-capture-fail")

        self.assertEqual(result.returncode, 1)
        payload = self.parse_output(result)
        self.assertTrue(payload["results"][0]["success"])
        self.assertFalse(payload["results"][1]["success"])
        self.assertEqual(payload["results"][1]["error"], "capture_failed")

    def test_capture_is_removed_when_ttl_cleanup_cannot_be_scheduled(self) -> None:
        result = self.run_script("dylan", nohup_bin=self.fake_bin / "missing-nohup")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.parse_output(result)["error"], "cleanup_schedule_failed")
        self.assertEqual(list(self.capture_dir.glob("findmy-*.png")), [])

    def test_cleanup_removes_only_capture_files(self) -> None:
        self.capture_dir.mkdir(parents=True)
        capture = self.capture_dir / "findmy-dylan-old.png"
        unrelated = self.capture_dir / "notes.txt"
        capture.write_bytes(b"private")
        unrelated.write_text("keep", encoding="utf-8")

        result = self.run_script("cleanup")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.parse_output(result)["deleted"], 1)
        self.assertFalse(capture.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.calls(), [])

    def test_invalid_target_and_ttl_do_not_open_find_my(self) -> None:
        unknown = self.run_script("someone-else")
        self.assertEqual(unknown.returncode, 2)
        self.assertEqual(self.parse_output(unknown)["error"], "unknown_person")
        self.assertEqual(self.calls(), [])

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.root / "home"),
                "FAKE_LOG": str(self.log),
                "FINDMY_CAPTURE_DIR": str(self.capture_dir),
                "FINDMY_CAPTURE_TTL_SECONDS": "5",
                "FINDMY_OPEN_BIN": str(self.open_bin),
                "FINDMY_PEEKABOO_BIN": str(self.peekaboo_bin),
                "FINDMY_NOHUP_BIN": str(self.nohup_bin),
                "FINDMY_SLEEP_BIN": str(self.sleep_bin),
            }
        )
        invalid_ttl = subprocess.run(
            ["bash", str(SCRIPT), "dylan"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(invalid_ttl.returncode, 2)
        self.assertEqual(self.parse_output(invalid_ttl)["error"], "invalid_ttl")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
