# Claude Code Hooks

Hooks are scripts that run in response to Claude Code lifecycle events. They live in `~/.claude/hooks/` and are registered in `~/.claude/settings.json`.

## Deployment Model: Copies, Not Symlinks

Hooks are deployed as **file copies**, not symlinks. This differs from skills and commands, which use symlinks to the dotfiles repo.

**Why:** OpenClaw v2026.3.7+ rejects symlinked files whose `realPath` resolves outside the configured `rootDir`. When hooks were symlinked to `~/repos/dotfiles/`, they resolved outside `~/.claude/` and broke. See `openclaw/plans/archive/skills-symlink-fix.md` for the full incident writeup.

**How it works:**
- `install.sh` copies hooks from the repo to `~/.claude/hooks/` (and auto-migrates any existing symlinks to copies)
- `sync.sh add hook <name>` copies to the repo but keeps the original file in place
- `sync.sh status` detects content-matching copies as "synced" via `diff -q`

**To update a hook after editing the repo copy:** run `./install.sh` or manually copy the file.

## Current Hooks

### no-rm.mjs

| Field | Value |
|-------|-------|
| Event | `PreToolUse` |
| Matcher | `Bash` |
| Language | Node.js |
| Source | [zcaceres/claude-rm-rf](https://github.com/zcaceres/claude-rm-rf) |

Blocks destructive `rm` commands and suggests `trash` instead. Parses the command string, strips quoted content to avoid false positives, and checks against a list of destructive patterns (`rm`, `shred`, `unlink`, `find -delete`, `xargs rm`, `sudo rm`, etc.). Allows `git rm` since it's version-controlled and recoverable.

Exit code 2 blocks the tool use; exit code 0 allows it.

### continuous-learning-activator.sh

| Field | Value |
|-------|-------|
| Event | `UserPromptSubmit` |
| Matcher | (all) |
| Language | Bash |

Injects a prompt reminder after every user message telling Claude to evaluate whether the interaction produced extractable knowledge. If so, Claude activates the `continuous-learning` skill to decide whether to create a new skill file.

This is the mechanism that grows the skill library over time.

### skill-to-dotfiles.sh

| Field | Value |
|-------|-------|
| Event | `PostToolUse` |
| Matcher | `Write\|Edit` |
| Language | Bash (reads JSON via Python) |

When Claude writes a file under `~/.claude/skills/`, this hook copies the skill directory to `~/repos/dotfiles/.claude/skills/` and replaces the original with a symlink. This auto-tracks new skills in the dotfiles repo without manual `sync.sh add` steps.

Only fires for paths matching `~/.claude/skills/*`. Skips files already symlinked to the dotfiles repo. Requires the dotfiles repo to exist at `~/repos/dotfiles`.

## Adding a New Hook

1. Create the script in `~/.claude/hooks/` (must have a shebang and be executable)
2. Register it in `~/.claude/settings.json` under the appropriate event
3. Track it: `cd ~/repos/dotfiles && ./sync.sh add hook <filename>`
4. Push: `./sync.sh push "Add <hook-name> hook"`

## Hook Events Reference

| Event | When | Use For |
|-------|------|---------|
| `PreToolUse` | Before a tool runs | Blocking dangerous operations, validation |
| `PostToolUse` | After a tool runs | Auto-tracking, side effects |
| `UserPromptSubmit` | After user sends a message | Prompt injection, reminders |

Each hook receives JSON on stdin with tool context. Exit code 0 allows, exit code 2 blocks (PreToolUse only).

## settings.json Hook Config

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node ~/.claude/hooks/no-rm.mjs"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/continuous-learning-activator.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/skill-to-dotfiles.sh"
          }
        ]
      }
    ]
  }
}
```
