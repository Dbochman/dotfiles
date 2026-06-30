#!/usr/bin/env python3
"""Regression tests for safety-critical canonical cron prompt contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


JOBS_PATH = Path(__file__).parents[1] / "cron" / "jobs.json"


class CronPromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(JOBS_PATH.read_text())
        cls.jobs = {job["id"]: job for job in payload["jobs"]}

    def test_julia_triage_uses_raw_api_environment_account_routing(self) -> None:
        job = self.jobs["gws-julia-morning-triage-0001"]
        prompt = job["payload"]["message"]

        self.assertIn("GOOGLE_WORKSPACE_CLI_ACCOUNT", prompt)
        self.assertNotIn("--account", prompt)
        self.assertIn(
            "Retry once after 5 seconds only when the response contains the "
            'exact transient error `"Failed to get token"`',
            prompt,
        )
        self.assertIn(
            "A `No credentials provided` response is a non-retryable "
            "account-routing error",
            prompt,
        )
        self.assertEqual(job["payload"]["timeoutSeconds"], 900)
        self.assertEqual(job["delivery"]["mode"], "none")
        self.assertEqual(job["schedule"]["expr"], "45 6 * * *")
        self.assertEqual(job["schedule"]["tz"], "America/New_York")


if __name__ == "__main__":
    unittest.main()
