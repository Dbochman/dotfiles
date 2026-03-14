---
name: openai-codex-cli-usage
description: |
  Correct usage patterns for OpenAI Codex CLI (@openai/codex). Use when: (1) Need to run
  Codex for plan/doc review (use exec not review), (2) Getting "config profile not found"
  errors from -p flag, (3) Want non-interactive Codex execution, (4) Need to understand
  the difference between codex review and codex exec commands, (5) Need structured JSON
  output from Codex, (6) Want web search in exec mode.
author: Claude Code
version: 2.0.0
date: 2026-03-14
---

# OpenAI Codex CLI Usage Patterns

## Problem

Confusion about how to invoke Codex CLI for different tasks. Common mistakes include:
- Using `-p` flag for prompts (it's for config profiles, not prompts)
- Using `codex review` for documentation review (only works for code diffs)
- Using `--search` in exec mode (doesn't work — use `-c web_search=live`)
- Not knowing about structured output with `--output-schema`

## Key Commands

| Task | Command |
|------|---------|
| Interactive session | `codex "your prompt"` |
| Non-interactive execution | `codex exec "your prompt"` or `codex e "your prompt"` |
| Code review (diffs only) | `codex exec review --base main` |
| Review specific commit | `codex exec review --commit abc123` |
| Review uncommitted changes | `codex exec review --uncommitted` |
| Resume last session | `codex exec resume --last "follow up"` |
| Review file content | `codex exec "review this: $(cat file.md)"` |

## Exec Mode Patterns

### Basic one-shot (ephemeral, no disk persistence)
```bash
codex exec --ephemeral "analyze this codebase for security issues"
```

### With live web search
```bash
# WRONG — --search flag doesn't work with exec
codex exec --search "latest React version"

# CORRECT — use config override
codex exec -c web_search=live "latest React version"
```

### JSONL output with jq parsing
```bash
# Stream events, extract agent messages
codex exec --json "summarize the repo" | jq -r 'select(.type == "item.completed") | .item.text // empty'

# Get token usage
codex exec --json "hello" | jq 'select(.type == "turn.completed") | .usage'
```

### Structured JSON output with schema
```bash
# Define schema (strict mode: additionalProperties: false, all props required)
cat > /tmp/schema.json << 'EOF'
{
  "type": "object",
  "properties": {
    "issues": { "type": "array", "items": { "type": "string" } },
    "severity": { "type": "string" },
    "summary": { "type": "string" }
  },
  "required": ["issues", "severity", "summary"],
  "additionalProperties": false
}
EOF

codex exec --ephemeral --output-schema /tmp/schema.json -o /tmp/result.json \
  "Analyze this code for issues"
```

### Commit review (catches real bugs)
```bash
codex exec --ephemeral "Review the last 5 commits for potential issues. Be concise."
```

### Full-auto with workspace write
```bash
# Let Codex make changes autonomously
codex exec --full-auto "fix the failing tests"

# Full auto is always workspace-write sandbox. For broader access:
codex exec --sandbox danger-full-access "your task"
# or equivalently:
codex exec --yolo "your task"
```

### Input methods
```bash
# Stdin pipe
cat logs.txt | codex exec -

# Here-doc for multi-line prompts
codex exec <<EOF
Review this code for bugs:
$(cat file.py)
Focus on: error handling, edge cases.
EOF

# Image input
codex exec "explain this error" -i screenshot.png
```

### Save output to file
```bash
# -o captures final message to file (stdout still gets it too)
codex exec "generate release notes" -o release-notes.md

# With JSONL: stdout gets events, -o gets just the final message
codex exec --json "task" -o result.txt
```

## Permission Modes

| Flag | Sandbox | Use Case |
|------|---------|----------|
| (default) | read-only | Safe analysis |
| `--full-auto` | workspace-write | Local automation |
| `--yolo` | danger-full-access | Isolated CI only |

Note: `--full-auto` silently overrides `--sandbox`. To get full access, use `--sandbox danger-full-access` directly.

## Session Management

```bash
# Ephemeral (no disk persistence)
codex exec --ephemeral "triage this repo"

# Resume most recent session
codex exec resume --last "now fix those issues"

# Resume specific session
codex exec resume SESSION_ID "follow up"
```

## Config Reference

Key settings in `~/.codex/config.toml`:
```toml
model = "gpt-5.4"
model_reasoning_effort = "high"    # minimal|low|medium|high|xhigh
model_reasoning_summary = "concise" # auto|concise|detailed|none
service_tier = "fast"
web_search = "live"                # disabled|cached|live
sandbox_mode = "workspace-write"

[features]
multi_agent = true
shell_snapshot = true

[agents]
max_threads = 6
max_depth = 1
```

## Gotchas

- `codex exec` requires a Git repo by default — use `--skip-git-repo-check` to override
- Progress streams to stderr, only final message goes to stdout
- `CODEX_API_KEY` only works with `codex exec`, not other commands
- Resuming an ephemeral session silently creates a new session (no error)
- `--output-schema` requires strict mode JSON Schema (`additionalProperties: false`)
- In exec mode, approval is always `never` (no interactive user to prompt)
