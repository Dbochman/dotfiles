import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SKILL = Path(__file__).resolve().parents[1] / "skills/plant-bloom-watch"
SPEC = importlib.util.spec_from_file_location("bloom_state", SKILL / "scripts/bloom_state.py")
bloom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bloom)


class BloomStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "state"
        self.state = self.root / "state.json"
        for name, value in (("ROOT", self.root), ("STATE", self.state)):
            patch = mock.patch.object(bloom, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, *arguments):
        with mock.patch("sys.argv", ["bloom_state.py", *arguments]), contextlib.redirect_stdout(io.StringIO()) as output:
            bloom.main()
        return json.loads(output.getvalue())

    def test_completed_read_preserves_bytes_and_does_not_write(self):
        state = bloom.default_state()
        state.update(rosesAlerted=True, gladiolusAlerted=True)
        bloom.save(state)
        before = self.state.read_bytes()
        modified = self.state.stat().st_mtime_ns
        with mock.patch.object(bloom, "save", side_effect=AssertionError("read must not write")):
            result = self.command("read")
        self.assertTrue(result["complete"])
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_checks_never_clear_once_only_alert_flags(self):
        self.command("mark-alert", "--kind", "roses")
        for value in ("yes", "no", "unclear"):
            result = self.command("record-check", "--roses", value, "--gladiolus", "no")
            self.assertTrue(result["rosesAlerted"])
            self.assertFalse(result["complete"])
        self.command("mark-alert", "--kind", "gladiolus")
        self.assertTrue(self.command("record-failure")["complete"])

    def test_failure_threshold_deduplication_and_recovery(self):
        self.assertFalse(self.command("record-failure")["shouldNotify"])
        self.assertFalse(self.command("record-failure")["shouldNotify"])
        self.assertTrue(self.command("record-failure")["shouldNotify"])
        self.command("mark-alert", "--kind", "failure")
        self.assertFalse(self.command("record-failure")["shouldNotify"])
        result = self.command("record-check", "--roses", "unclear", "--gladiolus", "no")
        self.assertEqual(result["failureCount"], 0)
        self.assertFalse(result["failureNotified"])

    def test_duplicate_alert_mark_is_idempotent(self):
        first = self.command("mark-alert", "--kind", "roses")
        second = self.command("mark-alert", "--kind", "roses")
        self.assertEqual(first, second)

    def test_private_atomic_storage(self):
        self.command("record-check", "--roses", "no", "--gladiolus", "unclear")
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_corruption_does_not_reset_state(self):
        bloom.save(bloom.default_state())
        self.state.write_text("{broken")
        with self.assertRaises(SystemExit):self.command("record-failure")
        self.assertEqual(self.state.read_text(), "{broken")

    def test_permissions_symlinks_and_hardlinks_rejected(self):
        bloom.save(bloom.default_state())
        self.state.chmod(0o644)
        with self.assertRaises(SystemExit):self.command("read")
        self.state.chmod(0o600)
        target = self.root / "target.json"
        self.state.rename(target)
        self.state.symlink_to(target)
        with self.assertRaises(SystemExit):self.command("read")
        self.state.unlink()
        os.link(target, self.state)
        with self.assertRaises(SystemExit):self.command("read")

    def test_invalid_schema_and_results_rejected(self):
        for change in ({"schema": 2}, {"rosesAlerted": "yes"}, {"failureCount": True}, {"lastResult": {"roses": "maybe", "gladiolus": "no"}}):
            with self.assertRaises(SystemExit):bloom.validate({**bloom.default_state(), **change})

    def test_managed_path_resolves_and_canonical_job_stays_disabled(self):
        instructions = (SKILL / "SKILL.md").read_text()
        self.assertIn("~/.openclaw/skills/plant-bloom-watch/scripts/bloom_state.py", instructions)
        self.assertNotIn("~/.openclaw/workspace/skills/plant-bloom-watch", instructions)
        jobs = json.loads((SKILL.parents[1] / "cron/jobs.json").read_text())["jobs"]
        job = next(job for job in jobs if job["id"] == "plant-watch-julia-blooms-2026")
        self.assertIs(job["enabled"], False)
        self.assertEqual(job["delivery"]["mode"], "none")
