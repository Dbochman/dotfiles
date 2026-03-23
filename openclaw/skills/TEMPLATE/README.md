# Skill Template

Copy this directory to create a new OpenClaw skill.

## Quick Start

```bash
cp -r openclaw/skills/TEMPLATE openclaw/skills/<new-skill-name>
# Edit SKILL.md with your skill details
# Create the CLI script or wrapper
```

## File Structure

```
openclaw/skills/<skill-name>/
  SKILL.md          # Skill definition (required)
  <cli-name>        # CLI script (if self-contained)
  <helper>.py       # Helper scripts (if needed)
```

## CLI Wrapper Convention

If the skill has a CLI, create a wrapper in `openclaw/bin/<cli-name>`:

```bash
#!/bin/bash
# <cli-name> — <description>
SKILL_DIR="$HOME/.openclaw/skills/<skill-name>"
exec "$SKILL_DIR/<cli-name>" "$@"
```

**Rules:**
- Always use wrapper scripts, NEVER symlinks (SCRIPT_DIR breaks with symlinks)
- Wrapper goes in `openclaw/bin/` (deployed to `~/.openclaw/bin/` on Mini)
- Verify no Homebrew name collision: `brew list --formula | grep '^<name>$'`
- Add `requires.bins` to SKILL.md metadata

## SKILL.md Frontmatter

```yaml
---
name: skill-name                          # kebab-case, unique
description: >-                           # when to trigger this skill
  Control X at Y. Use when asked about Z.
  NOT for W (use other-skill for that).
allowed-tools: Bash(skill-name:*)         # namespace for bash commands
metadata: {"openclaw":{"emoji":"X","requires":{"bins":["cli-name"]}}}
---
```

## Deployment

1. Commit to dotfiles repo
2. `dotfiles-pull.command` copies skills to `~/.openclaw/skills/` (real copies, not symlinks)
3. `dotfiles-pull.command` copies wrappers to `~/.openclaw/bin/`
4. Gateway hot-reloads skill changes — no restart needed

## API Patterns

### Cloud API (e.g., 8sleep, Cielo)
- CLI runs directly on Mac Mini
- Credentials in `~/.config/<service>/` or `~/.openclaw/.secrets-cache`
- Token caching in file (not keychain — avoid launchd issues)
- Set `user-agent` header if API checks it

### Local Network API (e.g., Roomba MQTT, Hue)
- If device is at Crosstown: CLI SSHs to MacBook Pro, runs command there
- macOS launchd blocks outbound TLS to LAN IPs — use SSH connect-per-request
- If device is at Cabin: CLI runs directly on Mac Mini (same LAN)

### Google Assistant (e.g., Cabin Roombas)
- Python venv with `gassist-text`
- OAuth credentials at `~/.openclaw/<service>/credentials.json`
- Use venv Python in shebang, not system Python (PEP 668)

## Checklist

- [ ] SKILL.md with frontmatter (name, description, allowed-tools, metadata)
- [ ] CLI script or wrapper
- [ ] Wrapper in `openclaw/bin/<cli-name>` (if applicable)
- [ ] `requires.bins` in metadata matches wrapper name
- [ ] No Homebrew name collision
- [ ] Examples in SKILL.md use bare command names (not raw paths)
- [ ] Disambiguation section if similar skills exist
- [ ] Troubleshooting section for common errors
- [ ] Added to `dotfiles-pull.command` wrapper deploy list
- [ ] Tested from Mac Mini: `<cli-name> status` (or equivalent)
