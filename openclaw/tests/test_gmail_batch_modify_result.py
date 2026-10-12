import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/gmail-batch-modify-result.py"
SPEC = importlib.util.spec_from_file_location("batch_result", SCRIPT)
result = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(result)


class BatchModifyResultTests(unittest.TestCase):
    def test_documented_empty_success(self):
        for output in (b"", b" \n", b"{}", b"{}\n"):
            with self.subTest(output=output):
                self.assertTrue(result.accepted(0, output))

    def test_errors_and_unknown_results_fail_closed(self):
        for output in (b'null', b'[]', b'{"error":"secret"}', b'{"ok":true}', b'not json', b'\xff', b' ' * (result.LIMIT + 1)):
            self.assertFalse(result.accepted(0, output))
        for code in (1, -1, 124):
            self.assertFalse(result.accepted(code, b""))
            self.assertFalse(result.accepted(code, b"{}"))

    def test_cli_never_echoes_input_and_requires_readback(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--exit-code", "0"], input=b'{"error":"private"}', capture_output=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout), {"status": "unconfirmed", "readbackRequired": True})
        self.assertNotIn(b"private", run.stdout + run.stderr)

    def test_both_prompts_use_captured_output_and_no_retry(self):
        jobs = json.loads((ROOT / "cron/jobs.json").read_text())["jobs"]
        selected = [job for job in jobs if job["id"] in ("gws-julia-morning-triage-0001", "b7119197-b8e5-4f7d-9af2-edd268637cc8")]
        self.assertEqual(len(selected), 2)
        for job in selected:
            prompt = job["payload"]["message"]
            for clause in ("gmail-batch-modify-result.py", "actual terminal exit code", "Never rerun", "read back the exact affected IDs", "not to reads or draft creation"):
                self.assertIn(clause, prompt)
