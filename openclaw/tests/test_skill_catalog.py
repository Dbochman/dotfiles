#!/usr/bin/env python3
"""Regression tests for the deployable OpenClaw skill catalog."""

from __future__ import annotations

import re
import runpy
import unittest
from pathlib import Path


OPENCLAW_DIR = Path(__file__).resolve().parents[1]
SKILLS_DIR = OPENCLAW_DIR / "skills"
AUTHORING_GUIDE_PATH = OPENCLAW_DIR / "SKILL-AUTHORING.md"
TEMPLATE_PATH = OPENCLAW_DIR / "templates" / "skill" / "SKILL.md"
VALIDATOR_PATH = SKILLS_DIR / "skill-creator" / "scripts" / "quick_validate.py"
NAME_PATTERN = re.compile(
    r"^name:\s*(['\"]?)([^'\"\r\n]+)\1\s*$", re.MULTILINE
)


def load_skill_validator():
    """Load the canonical validator without writing import bytecode."""
    namespace = runpy.run_path(str(VALIDATOR_PATH))
    return namespace["validate_skill"]


class SkillCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate_skill = staticmethod(load_skill_validator())
        cls.skill_dirs = sorted(
            path
            for path in SKILLS_DIR.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )

    def test_catalog_contains_discoverable_skills(self) -> None:
        self.assertTrue(self.skill_dirs, f"No skills discovered under {SKILLS_DIR}")

    def test_discoverable_skills_pass_canonical_validation(self) -> None:
        failures = []
        for skill_dir in self.skill_dirs:
            valid, message = self.validate_skill(skill_dir)
            if not valid:
                failures.append(f"{skill_dir.name}: {message}")

        self.assertFalse(failures, "\n" + "\n".join(failures))

    def test_skill_name_matches_directory(self) -> None:
        failures = []
        for skill_dir in self.skill_dirs:
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            match = NAME_PATTERN.search(content)
            actual = match.group(2).strip() if match else "<missing>"
            if actual != skill_dir.name:
                failures.append(
                    f"{skill_dir.name}: frontmatter name is {actual!r}"
                )

        self.assertFalse(failures, "\n" + "\n".join(failures))

    def test_placeholder_template_is_not_a_discoverable_skill(self) -> None:
        discovered_names = {path.name.casefold() for path in self.skill_dirs}
        self.assertNotIn("template", discovered_names)

    def test_authoring_template_lives_outside_the_catalog(self) -> None:
        self.assertTrue(AUTHORING_GUIDE_PATH.is_file())
        self.assertTrue(TEMPLATE_PATH.is_file())

    def test_skill_roots_do_not_contain_readmes(self) -> None:
        readmes = [
            str(skill_dir.relative_to(OPENCLAW_DIR) / "README.md")
            for skill_dir in self.skill_dirs
            if (skill_dir / "README.md").exists()
        ]
        self.assertFalse(
            readmes,
            "Skill roots should use SKILL.md or referenced resources, not README.md:\n"
            + "\n".join(readmes),
        )


if __name__ == "__main__":
    unittest.main()
