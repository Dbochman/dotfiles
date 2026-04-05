---
name: openclaw-skill-bins-path-mismatch
description: |
  Fix OpenClaw skills showing "needs setup" or "Missing requirements (bins)" even though
  the CLI wrapper exists in ~/.openclaw/bin/. Use when: (1) `openclaw skills list` shows
  skills as "needs setup" despite the binary being at ~/.openclaw/bin/<name>,
  (2) `openclaw skills check` lists custom skills under "Missing requirements",
  (3) the gateway runtime uses the skill fine but the CLI check fails. Root cause:
  the skill `requires.bins` check uses the system PATH (not the gateway's configured
  PATH), so binaries in ~/.openclaw/bin/ are invisible to it.
author: Claude Code
version: 1.0.0
date: 2026-04-05
---

# OpenClaw Skill `requires.bins` PATH Mismatch

## Problem
OpenClaw's skill requirements checker (`openclaw skills check`, `openclaw skills list`)
validates that required binaries exist by searching the PATH. However, custom CLI
wrappers installed at `~/.openclaw/bin/` are only on the PATH in the gateway's
LaunchAgent plist — not on the system PATH used by the CLI tools. This causes skills
to appear as "needs setup" even when they work fine at runtime.

When a skill shows "needs setup", OpenClaw may not load it into the agent's context,
meaning the agent won't know it has that capability.

## Context / Trigger Conditions
- Custom skills with `metadata.openclaw.requires.bins` in their SKILL.md frontmatter
- CLI wrappers live in `~/.openclaw/bin/` (not `/opt/homebrew/bin/` or `/usr/local/bin/`)
- `openclaw skills list` shows the skill as "needs setup"
- `openclaw skills check` shows skill under "Missing requirements"
- The gateway itself can invoke the skill fine (its plist includes `~/.openclaw/bin` in PATH)

## Solution
Symlink the CLI wrappers from `~/.openclaw/bin/` into `/opt/homebrew/bin/`:

```bash
for bin in 8sleep cielo crisismode crosstown-roomba fi-collar litter-robot mysa petlibro ring; do
  src="$HOME/.openclaw/bin/$bin"
  dst="/opt/homebrew/bin/$bin"
  if [ -f "$src" ] && [ ! -e "$dst" ]; then
    ln -s "$src" "$dst" && echo "linked: $bin"
  fi
done
```

**Note on symlink safety:** The wrappers in `~/.openclaw/bin/` set `SCRIPT_DIR` explicitly
to `$HOME/.openclaw/skills/<name>` (not via `dirname $0`), so symlinks don't break path
resolution. The earlier guidance "never symlinks — SCRIPT_DIR breaks" applies to skill
directories, not to these wrappers.

## Verification
1. Run `openclaw skills check` — custom skills should appear under "Ready to use"
2. Run `openclaw skills list` — skills should show "ready" not "needs setup"
3. Restart gateway to load newly-eligible skills:
   `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`

## Example
Before fix (11 custom skills broken):
```
Missing requirements:
  S 8sleep (bins: 8sleep)
  ❄ cielo-ac (bins: cielo)
  🏥 crisismode (bins: crisismode)
  ...
```

After fix:
```
Ready to use:
  S 8sleep
  ❄ cielo-ac
  🏥 crisismode
  ...
```

## Notes
- This should be added to `dotfiles-pull.command` so symlinks are recreated on deploy
- The `dog-walk` and `crosstown-routines` skills depend on multiple bins (e.g.,
  `fi-collar` AND `crosstown-roomba`) — all must be symlinked
- New custom skills added in the future need the same symlink treatment
- The root cause is that OpenClaw's skill checker doesn't read the gateway plist's
  EnvironmentVariables to discover the configured PATH
