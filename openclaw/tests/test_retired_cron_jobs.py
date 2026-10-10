import json
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RetiredCronJobTests(unittest.TestCase):
    def test_approved_retirements_cannot_reenter_deployable_definitions(self):
        canonical = json.loads((ROOT / "cron/jobs.json").read_text())
        archive = json.loads((ROOT / "cron/archive/retired-one-shots-2026-10-10.json").read_text())
        expected = {
            "datenight-aug-farmtotable", "datenight-sep-steakhouse",
            "datenight-oct-indian", "doubledate-q4-oct-mexican",
            "qd-booking-2026-10-sep15",
        } | {f"world-cup-briefing-2026-07-{day:02d}" for day in range(6, 20)}
        self.assertEqual({job["id"] for job in archive["jobs"]}, expected)
        self.assertEqual(len(archive["jobs"]), 19)
        self.assertFalse(archive["completionVerified"])
        self.assertFalse(expected & {job["id"] for job in canonical["jobs"]})
        active_scopes = json.loads((ROOT / "cron/restaurant-booking-scopes.json").read_text())["jobs"]
        retired_scopes = json.loads((ROOT / "cron/archive/retired-booking-scopes-2026-10-10.json").read_text())["jobs"]
        self.assertFalse(expected & active_scopes.keys())
        self.assertEqual(set(retired_scopes), {job_id for job_id in expected if not job_id.startswith("world-cup-")})
        cutoff = datetime(2026, 10, 10, tzinfo=timezone.utc)
        for job in archive["jobs"]:
            self.assertEqual(job["schedule"]["kind"], "at")
            self.assertTrue(job["deleteAfterRun"])
            self.assertLess(datetime.fromisoformat(job["schedule"]["at"].replace("Z", "+00:00")), cutoff)


if __name__ == "__main__":
    unittest.main()
