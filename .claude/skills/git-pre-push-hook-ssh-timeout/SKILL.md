---
name: git-pre-push-hook-ssh-timeout
description: |
  Debug "Connection to github.com closed by remote host" errors during git push. Use when:
  (1) Push fails with SSH connection closed error, (2) Pre-push hooks run slow API calls
  (Codex, OpenAI, external services), (3) Push works with --no-verify but fails normally.
  The SSH connection times out (~60s) while hooks run synchronously. Fix by removing
  redundant hooks (if CI covers it), making hooks async, or adding timeouts.
author: Claude Code
version: 1.0.0
date: 2026-01-24
---

# Git Pre-Push Hook SSH Timeout

## Problem
Pre-push hooks that make slow API calls (like AI code review) cause GitHub's SSH
connection to timeout and close, resulting in a misleading error message.

## Context / Trigger Conditions
- `git push` fails with: `Connection to github.com closed by remote host`
- You have a pre-push hook that calls external APIs (Codex review, linting services, etc.)
- Push works fine with `git push --no-verify`
- The hook output shows it's running but never completes

## Solution

### Option 1: Remove redundant hooks (Recommended)
If CI/CD already runs the same check, the pre-push hook is redundant:
```bash
rm .husky/pre-push  # or trash .husky/pre-push
```

### Option 2: Add a timeout
Kill the slow operation if it exceeds a threshold:
```bash
# In pre-push hook
timeout 30s codex review --base main || echo "Review timed out, proceeding"
```

### Option 3: Make it async (non-blocking)
Run the check in background and don't block the push:
```bash
# In pre-push hook
(codex review --base main > /tmp/review.log 2>&1 &)
echo "Review running in background..."
exit 0
```

### Option 4: Skip flag
Already present in most setups - document it for users:
```bash
SKIP_CODEX_REVIEW=1 git push
# or
git push --no-verify
```

## Root Cause
GitHub's SSH server has an idle connection timeout (~60 seconds). When a pre-push
hook runs a synchronous API call that takes longer, the SSH connection sits idle
waiting for the hook to complete. GitHub eventually closes the connection.

The error message doesn't indicate the hook is the problem - it looks like a
network/SSH issue.

## Verification
After fixing:
1. `git push` should complete without `--no-verify`
2. No "connection closed" errors
3. If using Option 1, verify CI still runs the check

## Example

Before (problematic):
```bash
# .husky/pre-push
codex review --base main  # Takes 60+ seconds, times out SSH
```

After (fixed - rely on CI):
```yaml
# .github/workflows/codex-review.yml already runs on PRs
# Just remove the hook entirely
```

## Notes
- This affects any slow synchronous operation in hooks, not just Codex
- The timeout varies by git host (GitHub ~60s, GitLab may differ)
- CI-based reviews are more reliable than local hooks (no API key issues, rate limits)
- If you need local pre-push validation, prefer fast operations (<10s)
