---
name: merge-and-doc
description: |
  Merge a PR and update documentation in one workflow. Use when: (1) Ready to merge
  a PR and want to update roadmap/changelog, (2) Need to verify merge success before
  documenting, (3) Want atomic merge + doc commits. Handles gh pr merge, verification,
  kanban updates, and session notes in sequence.
author: Claude Code
version: 1.0.0
date: 2026-02-04
user_invocable: true
---

# Merge and Document Workflow

## Problem

Merging PRs and updating documentation are separate steps that often get interrupted mid-way,
leaving docs out of sync with merged code. Sessions end after the merge but before the roadmap
or changelog gets updated.

## Context / Trigger Conditions

- User says "merge PR #X" or "merge and doc"
- PR is approved and ready to merge
- Want to ensure documentation is updated atomically with the merge

## Solution

### Step 1: Verify PR Status

```bash
gh pr view <PR_NUMBER> --json state,mergeable,reviews,title,body
```

Check that:
- State is OPEN
- Mergeable is true
- Has at least one approving review (or user explicitly overrides)

### Step 2: Merge the PR

```bash
gh pr merge <PR_NUMBER> --squash --delete-branch
```

Use `--squash` by default (per project conventions). Add `--admin` if user has rights and wants to bypass checks.

### Step 3: Verify Merge Success

```bash
git fetch origin main
git log --oneline -1 origin/main
```

Confirm the merge commit appears and matches the PR title.

### Step 4: Update Local Main

```bash
git checkout main
git pull origin main
```

### Step 5: Update Documentation

Based on the PR content, update relevant docs:

**For feature PRs:**
```bash
# Move kanban card to changelog
npm run kanban:move -- --board=roadmap --card=<card-id> --to=changelog
npm run precompile-kanban
```

**For bug fixes:**
- Add entry to session-notes.md if notable
- Move related kanban card if exists

**For all PRs:**
- Check if ROADMAP.md needs updating
- Check if any related cards need status changes

### Step 6: Commit Documentation Updates

```bash
git add .
git commit -m "docs: update roadmap after PR #<NUMBER> merge

Moved <card-name> to changelog.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
git push origin main
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| PR number | Yes | The PR to merge (e.g., `123` or `#123`) |
| `--admin` | No | Bypass branch protection (requires admin rights) |
| `--no-doc` | No | Skip documentation updates |

## Example Usage

```
/merge-and-doc 215
/merge-and-doc #215 --admin
/merge-and-doc 215 --no-doc
```

## Verification

After completion:
1. PR shows as merged on GitHub
2. Local main is up to date with remote
3. Kanban board reflects the change (if applicable)
4. `npm run precompile-kanban` passes validation

## Error Handling

**PR not mergeable:**
- Show the blocking reason (failing checks, conflicts, missing reviews)
- Ask user if they want to proceed with `--admin` or fix issues first

**Merge fails:**
- Show error message
- Do NOT proceed to documentation updates
- Suggest `gh pr view` to diagnose

**Doc update fails:**
- Complete what's possible
- Report which updates failed
- Leave user with actionable next steps

## Notes

- Always use `--squash` merge per project conventions
- Delete branch after merge (keeps repo clean)
- If no kanban card exists for the PR, skip kanban updates
- Session notes are optional—only add if the work was notable
- This skill follows the project's Git Workflow from CLAUDE.md
