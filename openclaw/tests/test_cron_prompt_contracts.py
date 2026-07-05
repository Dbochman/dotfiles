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

        self.assertIn("GOOGLE_WORKSPACE_CLI_ACCOUNT=${JULIA_EMAIL}", prompt)
        self.assertNotIn("--account", prompt)
        self.assertNotIn("STARMARKET_GMAIL", prompt)
        self.assertNotIn("TRYFI_EMAIL", prompt)
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

    def test_julia_briefing_uses_general_identity_and_environment_routing(self) -> None:
        prompt = self.jobs["gws-julia-morning-briefing-0001"]["payload"]["message"]

        self.assertIn("GOOGLE_WORKSPACE_CLI_ACCOUNT=${JULIA_EMAIL}", prompt)
        self.assertNotIn("--account", prompt)
        self.assertNotIn("STARMARKET_GMAIL", prompt)
        self.assertNotIn("TRYFI_EMAIL", prompt)

    def test_double_date_invites_use_general_identity_keys(self) -> None:
        for job_id in (
            "doubledate-q4-oct-mexican",
            "doubledate-q1-jan27-french",
        ):
            with self.subTest(job_id=job_id):
                prompt = self.jobs[job_id]["payload"]["message"]
                self.assertIn("${DYLAN_EMAIL}", prompt)
                self.assertIn("${JULIA_EMAIL}", prompt)
                self.assertNotIn("TRYFI_EMAIL", prompt)
                self.assertNotIn("STARMARKET_GMAIL", prompt)

    def test_restaurant_jobs_keep_standing_authorization_and_safeguards(self) -> None:
        restaurant_ids = [
            job_id
            for job_id in self.jobs
            if job_id.startswith(("datenight-", "doubledate-", "qd-booking-"))
        ]
        self.assertEqual(
            set(restaurant_ids),
            {
                "datenight-aug-farmtotable",
                "datenight-sep-steakhouse",
                "datenight-oct-indian",
                "datenight-nov-american",
                "datenight-dec-upscale",
                "doubledate-q4-oct-mexican",
                "doubledate-q1-jan27-french",
                "qd-booking-2026-10-sep15",
                "qd-booking-2027-01-dec15",
            },
        )
        for job_id in restaurant_ids:
            with self.subTest(job_id=job_id):
                job = self.jobs[job_id]
                self.assertTrue(job["enabled"])
                self.assertTrue(job["deleteAfterRun"])
                self.assertEqual(job["delivery"]["mode"], "none")
                prompt = self.jobs[job_id]["payload"]["message"]
                self.assertTrue(prompt.startswith("IDEMPOTENCY CHECK FIRST:"))
                self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", prompt)
                self.assertNotIn("op read", prompt)
                self.assertTrue(
                    "RESY_CACHE_ONLY=1" in prompt or "must remain cache-only" in prompt
                )

    def test_completed_july_restaurant_jobs_are_not_redeployable(self) -> None:
        self.assertNotIn("datenight-jul-japanese", self.jobs)
        self.assertNotIn("doubledate-q3-jul-korean", self.jobs)

    def test_weekly_finance_cron_requires_cache_only_credentials(self) -> None:
        prompt = self.jobs["financial-scrape-0001"]["payload"]["message"]

        self.assertIn("dedicated-cache-only credential scoping", prompt)
        self.assertIn("must never read `.env-token`", prompt)
        self.assertIn("invoke `op`", prompt)
        self.assertIn("1Password service-account token", prompt)
        self.assertIn("source finance credentials into the gateway environment", prompt)


if __name__ == "__main__":
    unittest.main()
