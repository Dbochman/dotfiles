#!/usr/bin/env python3
"""
Quick validation script for OpenClaw skills.

No external dependencies — uses regex-based frontmatter parsing
(pyyaml may not be installed on the Mac Mini).
"""

import re
import sys
from pathlib import Path


def validate_skill(skill_path):
    """Basic validation of a skill."""
    skill_path = Path(skill_path)

    # Check SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and validate frontmatter
    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    # Extract frontmatter
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    # Parse frontmatter fields with regex (no yaml dependency)
    # Extract top-level keys (lines starting without whitespace, containing a colon)
    top_level_keys = set()
    for line in frontmatter_text.split("\n"):
        key_match = re.match(r"^([a-zA-Z_-]+)\s*:", line)
        if key_match:
            top_level_keys.add(key_match.group(1))

    # OpenClaw allowed properties (superset of Claude Code's)
    ALLOWED_PROPERTIES = {
        "name", "description", "license", "allowed-tools",
        "metadata", "compatibility",
    }

    unexpected_keys = top_level_keys - ALLOWED_PROPERTIES
    if unexpected_keys:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_PROPERTIES))}"
        )

    # Check required fields exist
    if "name" not in top_level_keys:
        return False, "Missing 'name' in frontmatter"
    if "description" not in top_level_keys:
        return False, "Missing 'description' in frontmatter"

    # Extract and validate name
    name_match = re.search(r"^name:\s*(.+)$", frontmatter_text, re.MULTILINE)
    if name_match:
        name = name_match.group(1).strip().strip("\"'")
        if name:
            if not re.match(r"^[a-z0-9-]+$", name):
                return False, f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
            if name.startswith("-") or name.endswith("-") or "--" in name:
                return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
            if len(name) > 64:
                return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters."

    # Extract and validate description
    # Handle both single-line and multi-line (>, |) descriptions
    desc_match = re.search(r"^description:\s*(.+)$", frontmatter_text, re.MULTILINE)
    if desc_match:
        desc_value = desc_match.group(1).strip()
        # If it's a block scalar indicator, read continuation lines
        if desc_value in (">", "|", ">-", "|-"):
            desc_lines = []
            in_desc = False
            for line in frontmatter_text.split("\n"):
                if line.startswith("description:"):
                    in_desc = True
                    continue
                if in_desc:
                    if line.startswith("  ") or line.startswith("\t"):
                        desc_lines.append(line.strip())
                    else:
                        break
            description = " ".join(desc_lines)
        else:
            description = desc_value.strip("\"'")

        if description:
            if "<" in description or ">" in description:
                # Allow > and | for YAML block scalars at start of description value
                # Only flag actual angle brackets in the description text
                pass
            if len(description) > 1024:
                return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters."

    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
