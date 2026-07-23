#!/usr/bin/env python3
"""Contract tests for the read-only home-events OpenClaw skill."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "openclaw" / "skills" / "home-events" / "SKILL.md"


class HomeEventsSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = SKILL.read_text(encoding="utf-8")
        parts = cls.content.split("---", 2)
        if len(parts) != 3:
            raise AssertionError("skill frontmatter is missing")
        cls.frontmatter = parts[1]

    def test_skill_is_read_only_and_requires_safe_cli(self) -> None:
        self.assertIn("name: home-events", self.content)
        self.assertIn("allowed-tools: Bash(home-events:*)", self.content)
        self.assertEqual(
            [
                line.strip()
                for line in self.frontmatter.splitlines()
                if line.strip().startswith("allowed-tools:")
            ],
            ["allowed-tools: Bash(home-events:*)"],
        )
        self.assertIn('"bins":["home-events"]', self.content)
        self.assertNotIn("Bash(home-eventctl:*)", self.content)
        self.assertNotIn("allowed-tools: message", self.content)
        self.assertNotIn("why OpenClaw alerted", self.content)

    def test_skill_documents_all_agent_query_surfaces(self) -> None:
        for command in (
            "home-events status --json",
            "home-events recent",
            "home-events incidents",
            "home-events explain",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.content)
        self.assertIn("`decisions` array", self.content)

    def test_media_requires_explicit_nest_camera_workflow(self) -> None:
        self.assertIn("stores no historical media", self.content)
        self.assertIn("explicitly", self.content)
        self.assertIn("`nest-camera`", self.content)
        self.assertIn("Living Room Wired", self.content)
        self.assertIn("Never capture merely because an incident exists", self.content)

    def test_local_presence_language_preserves_evidence_limits(self) -> None:
        for event_type in (
            "presence.local_departure_inferred",
            "presence.local_arrival_observed",
            "presence.household_excursion_started",
            "presence.household_excursion_ended",
        ):
            with self.subTest(event_type=event_type):
                self.assertIn(event_type, self.content)
        self.assertIn("not proof", self.content)
        self.assertIn("physical whereabouts", self.content)
        self.assertIn("not open or resolve incidents", self.content)

    def test_skill_forbids_admin_presence_and_lock_mutations(self) -> None:
        self.assertIn("Never invoke `home-eventctl`", self.content)
        self.assertIn("Never call August lock or unlock", self.content)
        self.assertIn("Never run a live presence scan", self.content)


if __name__ == "__main__":
    unittest.main()
