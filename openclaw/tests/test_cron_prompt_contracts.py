#!/usr/bin/env python3
"""Regression tests for safety-critical canonical cron prompt contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


JOBS_PATH = Path(__file__).parents[1] / "cron" / "jobs.json"
SCOPES_PATH = Path(__file__).parents[1] / "cron" / "restaurant-booking-scopes.json"

RESTAURANT_JOBS = {
    "datenight-aug-farmtotable": {
        "at": "2026-08-01T12:00:00.000Z",
        "tokens": (
            "Farm-to-Table",
            "Newton or Brookline",
            "2 people",
            "August 7, 14, or 21, 2026",
        ),
    },
    "datenight-sep-steakhouse": {
        "at": "2026-09-01T12:00:00.000Z",
        "tokens": (
            "American or Steakhouse",
            "Newton or Brookline",
            "2 people",
            "September 11, 18, or 25, 2026",
        ),
    },
    "datenight-oct-indian": {
        "at": "2026-10-01T12:00:00.000Z",
        "tokens": (
            "Indian",
            "Newton or Brookline",
            "2 people",
            "October 9, 16, or 23, 2026",
        ),
    },
    "datenight-nov-american": {
        "at": "2026-11-01T12:00:00.000Z",
        "tokens": (
            "Modern American",
            "Newton or Brookline",
            "2 people",
            "November 6, 13, or 20, 2026",
        ),
    },
    "datenight-dec-upscale": {
        "at": "2026-12-01T12:00:00.000Z",
        "tokens": (
            "French, Italian, or Contemporary",
            "Newton or Brookline",
            "2 people",
            "December 4, 11, or 18, 2026",
        ),
    },
    "doubledate-q4-oct-mexican": {
        "at": "2026-10-01T14:00:00.000Z",
        "tokens": ("Mexican", "Brookline", "party of 4", "October 15 or 16, 2026"),
    },
    "doubledate-q1-jan27-french": {
        "at": "2027-01-02T12:00:00.000Z",
        "tokens": ("French", "Brookline", "party of 4", "January 14 or 15, 2027"),
    },
    "qd-booking-2026-10-sep15": {
        "at": "2026-09-15T14:00:00.000Z",
        "tokens": (
            "Brookline or Jamaica Plain",
            "party of 4",
            "6:30 PM",
            "October 9, 16, 23, or 30, 2026",
        ),
    },
    "qd-booking-2027-01-dec15": {
        "at": "2026-12-15T15:00:00.000Z",
        "tokens": (
            "Brookline or Jamaica Plain",
            "party of 4",
            "6:30 PM",
            "January 8, 15, 22, or 29, 2027",
        ),
    },
}


class CronPromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(JOBS_PATH.read_text())
        cls.jobs = {job["id"]: job for job in payload["jobs"]}
        cls.scopes = json.loads(SCOPES_PATH.read_text())["jobs"]

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

    def test_julia_briefing_uses_only_deterministic_collector(self) -> None:
        job = self.jobs["gws-julia-morning-briefing-0001"]
        prompt = job["payload"]["message"]
        helper = (
            "/usr/bin/python3 /Users/dbochman/dotfiles/openclaw/bin/"
            "julia-morning-briefing-data.py"
        )

        self.assertEqual(prompt.count(helper), 1)
        self.assertNotIn("GOOGLE_WORKSPACE_CLI_ACCOUNT", prompt)
        self.assertNotIn("gws calendar", prompt)
        self.assertNotIn("gws gmail", prompt)
        self.assertNotIn("set +e", prompt)
        self.assertNotIn("status=$?", prompt)
        self.assertIn("do not make any other tool calls", prompt)
        self.assertNotIn("STARMARKET_GMAIL", prompt)
        self.assertNotIn("TRYFI_EMAIL", prompt)
        self.assertEqual(job["payload"]["timeoutSeconds"], 240)

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
        self.assertEqual(set(restaurant_ids), set(RESTAURANT_JOBS))
        self.assertEqual(set(self.scopes), set(RESTAURANT_JOBS))
        for job_id in restaurant_ids:
            with self.subTest(job_id=job_id):
                job = self.jobs[job_id]
                self.assertTrue(job["enabled"])
                self.assertTrue(job["deleteAfterRun"])
                self.assertEqual(job["delivery"]["mode"], "none")
                self.assertEqual(job["schedule"]["kind"], "at")
                self.assertEqual(job["schedule"]["at"], RESTAURANT_JOBS[job_id]["at"])
                prompt = job["payload"]["message"]
                self.assertTrue(prompt.startswith("IDEMPOTENCY CHECK FIRST:"))
                command = f"`~/.openclaw/bin/restaurant-book run --job-id {job_id}`"
                self.assertEqual(prompt.count(command), 1)
                self.assertIn(
                    "the sole reservation-account idempotency check, restaurant "
                    "search, ranking, and booking path",
                    prompt,
                )
                self.assertIn("It searches both Resy and OpenTable", prompt)
                self.assertIn("This enabled canonical one-shot and its deployed scope authorize one surprise", prompt)
                self.assertIn("No durable exact-restaurant or exact-platform approval is required", prompt)
                self.assertIn(
                    "Across both providers, at most one total reservation "
                    "mutation is allowed",
                    prompt,
                )
                self.assertIn(
                    "do not retry and do not fall back to another provider, "
                    "restaurant, date, or time",
                    prompt,
                )
                self.assertIn("If `mutation_attempted` or `reservation_may_exist` is true", prompt)
                self.assertIn("send exactly one final status message yourself", prompt)
                self.assertIn("Do not perform another reservation action", prompt)
                self.assertNotIn("restaurant-book plan", prompt)
                self.assertNotIn("resy reservations", prompt.lower())
                self.assertNotIn("resy availability", prompt.lower())
                self.assertNotIn("resy book", prompt.lower())
                self.assertNotIn("opentable-book", prompt.lower())
                self.assertNotIn("opentable-reservations", prompt.lower())
                self.assertNotIn("confirmation number", prompt.lower())
                self.assertNotIn("RESY_CACHE_ONLY", prompt)
                self.assertNotIn(".secrets-cache", prompt)
                self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", prompt)
                self.assertNotIn("op read", prompt)
                for token in RESTAURANT_JOBS[job_id]["tokens"]:
                    self.assertIn(token, prompt)

                scope = self.scopes[job_id]
                self.assertEqual(scope["providers"], ["resy", "opentable"])
                self.assertEqual(scope["authorization"]["kind"], "canonical-cron-standing")
                self.assertEqual(scope["authorization"]["max_mutation_attempts"], 1)

    def test_restaurant_calendar_side_effects_follow_confirmation(self) -> None:
        for job_id in RESTAURANT_JOBS:
            prompt = self.jobs[job_id]["payload"]["message"]
            with self.subTest(job_id=job_id):
                if job_id.startswith("datenight-"):
                    self.assertIn(
                        "perform a complete and conclusive matching-event check",
                        prompt,
                    )
                    self.assertIn("create at most one event only when that check succeeds", prompt)
                    self.assertIn(
                        "If Calendar is unavailable, incomplete, malformed, or uncertain, "
                        "create no event",
                        prompt,
                    )
                    self.assertIn(
                        "For `already_reserved`, report the existing reservation "
                        "and do not create an event",
                        prompt,
                    )
                else:
                    self.assertIn("Inspect Julia's Google Calendar", prompt)
                    self.assertIn(
                        "Treat the precheck as clear only when the Calendar query succeeds",
                        prompt,
                    )
                    self.assertIn(
                        "If Calendar is unavailable, authentication fails, pagination "
                        "is incomplete, or any result is malformed or uncertain",
                        prompt,
                    )
                    self.assertIn(
                        "That repeat check must meet the same complete and conclusive standard",
                        prompt,
                    )
                    self.assertIn(
                        "if it does not, do not create or update any event",
                        prompt,
                    )
                    self.assertIn("If a matching event exists, reuse it", prompt)
                    self.assertIn(
                        "Include a confirmation/reference only if the coordinator "
                        "result explicitly returns one",
                        prompt,
                    )
                    self.assertIn(
                        "never fetch, derive, or invent it",
                        prompt,
                    )
                    self.assertIn(
                        "Do not create a calendar event for any non-confirmed "
                        "coordinator result",
                        prompt,
                    )

    def test_restaurant_scope_metadata_matches_the_standing_authorization(self) -> None:
        self.assertEqual(
            self.scopes["datenight-aug-farmtotable"]["search"]["eligible_cuisine_terms"],
            ["Farm-to-Table"],
        )
        self.assertEqual(
            self.scopes["datenight-nov-american"]["search"]["eligible_cuisine_terms"],
            ["Modern American", "New American"],
        )
        self.assertEqual(
            self.scopes["datenight-dec-upscale"]["search"]["minimum_price_tier"],
            3,
        )
        for job_id in set(RESTAURANT_JOBS) - {"datenight-dec-upscale"}:
            with self.subTest(job_id=job_id):
                self.assertIsNone(self.scopes[job_id]["search"]["minimum_price_tier"])
        for job_id in ("qd-booking-2026-10-sep15", "qd-booking-2027-01-dec15"):
            with self.subTest(job_id=job_id):
                prompt = self.jobs[job_id]["payload"]["message"]
                self.assertNotIn("varying from previous quarters", prompt)
                self.assertNotIn("choosing a fresh restaurant", prompt)

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
