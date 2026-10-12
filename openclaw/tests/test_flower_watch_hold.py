import json
from pathlib import Path
import unittest


class FlowerWatchHoldTests(unittest.TestCase):
    def test_deployment_preserves_disabled_job_and_history_identity(self):
        path = Path(__file__).resolve().parents[1] / "cron/jobs.json"
        jobs = json.loads(path.read_text())["jobs"]
        matches = [job for job in jobs if job["id"] == "plant-watch-julia-blooms-2026"]
        self.assertEqual(len(matches), 1)
        self.assertIs(matches[0]["enabled"], False)
        self.assertEqual(matches[0]["name"], "Julia Flower Bloom Watch")
        self.assertEqual(matches[0]["schedule"]["expr"], "0 8 * * *")
