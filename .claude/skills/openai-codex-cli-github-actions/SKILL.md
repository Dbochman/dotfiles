---
name: openai-codex-cli-github-actions
description: |
  Configure OpenAI Codex CLI (@openai/codex) for automated PR reviews in GitHub Actions.
  Use when: (1) Getting "401 Missing bearer or basic authentication" errors, (2) Error
  "the argument '--base <BRANCH>' cannot be used with '[PROMPT]'", (3) Error "--commit
  cannot be used with --base", (4) Codex review not authenticating despite OPENAI_API_KEY
  secret being set. Covers the required login step and correct argument combinations.
author: Claude Code
version: 1.0.0
date: 2026-01-20
---

# OpenAI Codex CLI in GitHub Actions

## Problem
Setting up the OpenAI Codex CLI (`@openai/codex`) for automated PR reviews in GitHub
Actions fails with authentication errors or argument conflicts, even when `OPENAI_API_KEY`
is properly configured as a repository secret.

## Context / Trigger Conditions

You'll hit this when:
- Setting up automated code review using Codex CLI in GitHub Actions
- Error: `401 Missing bearer or basic authentication in header`
- Error: `the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`
- Error: `the argument '--commit <SHA>' cannot be used with: --base <BRANCH>`
- The `OPENAI_API_KEY` secret is set but reviews still fail with auth errors

## Solution

### 1. Authentication Requires Explicit Login

The Codex CLI does NOT automatically use `OPENAI_API_KEY` environment variable. You must
explicitly log in by piping the key to stdin:

```yaml
- name: Run Codex review
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: |
    # REQUIRED: Authenticate before running review
    echo "$OPENAI_API_KEY" | codex login --with-api-key

    # Now run the review
    codex review --base origin/main --title "PR Title"
```

### 2. Argument Combinations

The `codex review` command has mutually exclusive options:

| Use Case | Command |
|----------|---------|
| Review PR diff against base | `codex review --base origin/main` |
| Review specific commit | `codex review --commit <SHA>` |
| Review uncommitted changes | `codex review --uncommitted` |

**Cannot combine:**
- `--base` with `--commit`
- `--base` with custom `[PROMPT]` argument (remove the prompt)

### 3. Complete Working Workflow

```yaml
name: Codex PR Review

on:
  pull_request:
    branches: [main]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install Codex CLI
        run: npm install -g @openai/codex

      - name: Run Codex review
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          echo "$OPENAI_API_KEY" | codex login --with-api-key

          codex review \
            --base origin/${{ github.base_ref }} \
            --title "${{ github.event.pull_request.title }}" \
            > review_output.txt 2>&1 || true

      - name: Post comment
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh pr comment ${{ github.event.pull_request.number }} \
            --body-file review_output.txt
```

## Verification

1. Check workflow logs for `Reading API key from stdin... Successfully logged in`
2. Review output should show `model: gpt-5.2-codex` and actual code analysis
3. PR comment should contain review findings, not auth errors

## Common Mistakes

| Mistake | Result | Fix |
|---------|--------|-----|
| Missing login step | 401 Unauthorized | Add `echo "$KEY" \| codex login --with-api-key` |
| Using `--base` + `--commit` | Argument conflict error | Use only one |
| Adding custom prompt with `--base` | Argument conflict error | Remove the prompt |
| Expecting `OPENAI_API_KEY` env to work automatically | 401 error | Must explicitly login |

## Notes

- The Codex CLI is labeled "research preview" and uses model `gpt-5.2-codex`
- Error messages can be misleading (usage lines suggest combinations that don't work)
- `fetch-depth: 0` is required for `--base` to work (needs git history)
- The CLI stores credentials after login, so login only needed once per job

## References

- OpenAI Codex CLI: `npx @openai/codex --help`
- GitHub Actions secrets: https://docs.github.com/en/actions/security-guides/encrypted-secrets
