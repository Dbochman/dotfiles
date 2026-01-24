---
name: github-actions-cancel-on-pr-merge
description: |
  Cancel in-progress GitHub Actions workflows when a PR is merged or closed. Use when:
  (1) Slow CI jobs (like Codex reviews) waste minutes after PR merges, (2) You have
  `cancel-in-progress: true` but workflows still run after merge, (3) You want to
  save CI minutes by stopping obsolete workflow runs. Covers the `closed` event type
  pattern with concurrency groups.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# Cancel GitHub Actions Workflows on PR Merge

## Problem

GitHub Actions workflows triggered by `pull_request` events continue running even after the PR is merged or closed. The `cancel-in-progress: true` concurrency setting only cancels when new commits are pushed, not when the PR is closed.

## Context / Trigger Conditions

- Slow CI jobs (like AI code reviews) continue running after PR merge
- You're paying for CI minutes that provide no value
- You have `cancel-in-progress: true` but it doesn't help on merge
- Workflow runs show as "in progress" after PR is already merged

## Solution

Add `closed` to the PR event types and add a job condition to skip running on close:

```yaml
name: PR Workflow

on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, closed]  # Add 'closed'

# Cancel in-progress runs when PR is closed/merged
concurrency:
  group: workflow-name-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  my-job:
    runs-on: ubuntu-latest
    # Skip if PR is closed - concurrency cancels in-progress runs
    if: github.event.action != 'closed'
    steps:
      # ... job steps
```

## How It Works

1. When a PR is merged/closed, GitHub fires the `closed` event
2. This triggers the workflow with `github.event.action == 'closed'`
3. The concurrency group matches any in-progress run for this PR
4. `cancel-in-progress: true` cancels the old run
5. The new run's job condition `github.event.action != 'closed'` causes it to skip
6. Net result: old run cancelled, no new run started

## Verification

1. Open a PR and let CI start running
2. Merge the PR while CI is still in progress
3. Check the Actions tab - the in-progress run should be cancelled
4. Verify no new "closed" run is started (or it's skipped immediately)

## Example

Before (workflows continue after merge):
```yaml
on:
  pull_request:
    branches: [main]
    types: [opened, synchronize]

concurrency:
  group: ci-${{ github.event.pull_request.number }}
  cancel-in-progress: true
```

After (workflows cancel on merge):
```yaml
on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, closed]

concurrency:
  group: ci-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  build:
    if: github.event.action != 'closed'
    # ...
```

## Notes

- The job-level `if` condition is important - without it, a new job would start and immediately succeed, which still wastes a runner startup
- This pattern works for any PR-triggered workflow (tests, reviews, deployments)
- For workflows that should run on close (like cleanup), use a separate job with `if: github.event.action == 'closed'`
- The concurrency group should include the PR number to scope cancellation correctly

## References

- [GitHub Actions: Events that trigger workflows](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#pull_request)
- [GitHub Actions: Using concurrency](https://docs.github.com/en/actions/using-jobs/using-concurrency)
