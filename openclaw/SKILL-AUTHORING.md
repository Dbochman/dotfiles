# OpenClaw Skill Authoring

Use the non-deployable template at `openclaw/templates/skill/` to create a new
OpenClaw skill. Keep authoring templates and supporting documentation outside
`openclaw/skills/`; every immediate child of that directory with a `SKILL.md`
is treated as a deployable runtime skill.

## Quick start

```bash
cp -R openclaw/templates/skill openclaw/skills/<new-skill-name>
# Edit SKILL.md with the skill details and add only the resources it needs.
```

Use a lowercase, kebab-case directory name that exactly matches the skill's
frontmatter `name`. Do not leave placeholder values in a deployable skill.

## File structure

```text
openclaw/skills/<skill-name>/
  SKILL.md          # Skill definition (required)
  <cli-name>        # CLI script (if self-contained)
  <helper>.py       # Helper scripts (if needed)
  scripts/          # Deterministic helpers (optional)
  references/       # Details loaded only when needed (optional)
  assets/           # Output resources (optional)
```

Do not add `README.md`, setup guides, changelogs, or other author-facing
documentation inside a skill. Put repository documentation under `openclaw/`
or `docs/`; keep the deployed skill limited to instructions and resources the
agent needs at runtime.

## CLI wrapper convention

If the skill has a CLI, create a wrapper in `openclaw/bin/<cli-name>`:

```bash
#!/usr/bin/env bash
# <cli-name> — <description>
SKILL_DIR="$HOME/.openclaw/skills/<skill-name>"
exec "$SKILL_DIR/<cli-name>" "$@"
```

Follow these rules:

- Use wrapper scripts, not symlinks; scripts that resolve siblings through
  `SCRIPT_DIR` break when invoked through a symlink.
- Put wrappers in `openclaw/bin/`; deployment copies them to
  `~/.openclaw/bin/` on the Mini.
- Check for a Homebrew command collision before choosing the name:
  `brew list --formula | grep -Fx '<cli-name>'`.
- Add the wrapper to `metadata.openclaw.requires.bins` in `SKILL.md`.

## Frontmatter

```yaml
---
name: skill-name
description: >-
  Control X at Y. Use when asked about Z. Do not use for W; use other-skill.
allowed-tools: Bash(skill-name:*)
metadata: {"openclaw":{"emoji":"X","requires":{"bins":["cli-name"]}}}
---
```

Make the description state both what the skill does and when it should
trigger. Put trigger and disambiguation information in the description because
OpenClaw reads it before deciding whether to load the body.

## Deployment

1. Commit the skill to the dotfiles repository.
2. `dotfiles-pull.command` copies deployable skills to
   `~/.openclaw/skills/` as real directories, not symlinks.
3. It copies executable wrappers from `openclaw/bin/` to
   `~/.openclaw/bin/`.
4. The gateway hot-reloads skill changes; a restart is normally unnecessary.

## API patterns

### Cloud API

- Run the CLI directly on the Mac Mini.
- Resolve credentials through the repository's current 1Password/cache
  contract; never place secret values in a skill or its logs.
- Cache renewable tokens in protected runtime state rather than the tracked
  skill directory.
- Set an appropriate user-agent when the service requires one.

### Local network API

- For a device at Crosstown, SSH to the designated host and run the command
  there.
- For a device at the Cabin, run locally on the Mac Mini when it shares the
  device's network.
- Prefer a connect-per-request workflow when the LaunchAgent environment
  cannot reach the LAN service reliably.

### Dedicated Python environment

- Pin dependencies in the skill when a dedicated virtual environment is
  required.
- Use the virtual environment's interpreter in wrappers rather than relying on
  the system Python.

## Checklist

- [ ] `SKILL.md` has a matching kebab-case `name` and a specific description.
- [ ] The skill contains no placeholder values or author-only documentation.
- [ ] Tool access is limited to what the workflow needs.
- [ ] High-impact actions require explicit confirmation and postcondition checks.
- [ ] A CLI script and `openclaw/bin/` wrapper exist when needed.
- [ ] `requires.bins` matches the wrapper name and has no Homebrew collision.
- [ ] Examples use the deployed command name rather than repository paths.
- [ ] Similar skills are disambiguated in the frontmatter description.
- [ ] Deterministic behavior has targeted tests or a safe dry-run check.
- [ ] The skill passes `openclaw/skills/skill-creator/scripts/quick_validate.py`.
- [ ] The skill was tested from the Mac Mini with a read-only command first.
