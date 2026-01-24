---
name: openai-codex-cli-usage
description: |
  Correct usage patterns for OpenAI Codex CLI (@openai/codex). Use when: (1) Need to run
  Codex for plan/doc review (use exec not review), (2) Getting "config profile not found"
  errors from -p flag, (3) Want non-interactive Codex execution, (4) Need to understand
  the difference between codex review and codex exec commands.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# OpenAI Codex CLI Usage Patterns

## Problem

Confusion about how to invoke Codex CLI for different tasks. Common mistakes include:
- Using `-p` flag for prompts (doesn't exist)
- Using `codex review` for documentation review (only works for code diffs)
- Not knowing how to run non-interactive prompts

## Context / Trigger Conditions

- Need to review a plan, documentation, or non-code file with Codex
- Getting error: `config profile 'Your prompt here' not found`
- Want to run Codex without interactive session
- Need automated CI/CD code review

## Solution

### Key Commands

| Task | Command |
|------|---------|
| Interactive session | `codex "your prompt"` |
| Non-interactive execution | `codex exec "your prompt"` |
| Code review (diffs only) | `codex review --base main` |
| Review specific file content | `codex exec "review this: $(cat file.md)"` |

### Common Patterns

**Review a plan or documentation file:**
```bash
# WRONG - review only works on git diffs
codex review docs/plans/my-plan.md

# CORRECT - use exec with the content
codex exec "Review this plan for issues: $(cat docs/plans/my-plan.md)"
```

**Non-interactive with JSON output:**
```bash
codex exec "Analyze this code for security issues" --json
```

**Code review for PR:**
```bash
# Review changes against main branch
codex review --base main

# Review changes against specific commit
codex review --base abc123
```

**With specific model:**
```bash
codex exec -m gpt-4o "Your prompt here"
```

### Pre-push Hook Pattern

```bash
#!/bin/sh
# .husky/pre-push
codex review --base main 2>&1
exit_code=$?

if echo "$output" | grep -q "Verdict:.*Needs Changes"; then
  echo "❌ Codex review found issues"
  exit 1
fi
```

## Verification

- `codex exec "hello"` should return a response without error
- `codex review --base main` should analyze uncommitted changes
- `codex --help` shows all available commands

## Notes

- `codex review` is specifically for reviewing git diffs, not arbitrary files
- For reviewing documentation/plans, pipe content to `codex exec`
- The `-p` flag is for config profiles, not prompts
- Use `--json` flag for structured output in automation

## References

- Run `codex --help` and `codex exec --help` for full options
- Codex sessions stored in `~/.codex/sessions/`
