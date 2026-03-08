#!/bin/bash
# dotfiles-pull.command — Auto-pull dotfiles repo to keep symlinked skills in sync
# Runs as a LaunchAgent daily via Terminal.app (for git credential access)
# Improved: stashes local changes instead of skipping entirely

LOG="$HOME/.openclaw/logs/dotfiles-pull.log"
REPO="$HOME/dotfiles"

cd "$REPO" || exit 1

DIRTY=$(git status --porcelain)

if [ -n "$DIRTY" ]; then
  # Stash local changes, pull, then reapply
  STASH_OUT=$(git stash push -m "auto-stash before daily pull" 2>&1)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Stashed local changes: $STASH_OUT" >> "$LOG"

  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$PULL_STATUS $PULL_OUT" >> "$LOG"

  POP_OUT=$(git stash pop 2>&1)
  POP_STATUS=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) stash-pop exit=$POP_STATUS $POP_OUT" >> "$LOG"

  if [ $POP_STATUS -ne 0 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARNING: stash pop had conflicts, dropping stash" >> "$LOG"
    git checkout --theirs . 2>/dev/null
    git stash drop 2>/dev/null
  fi
else
  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$PULL_STATUS $PULL_OUT" >> "$LOG"
fi

# Deploy updated cron job definitions (preserves runtime state)
if [ -x "$REPO/openclaw/sync-cron-jobs.sh" ]; then
  SYNC_OUT=$("$REPO/openclaw/sync-cron-jobs.sh" deploy 2>&1)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sync-cron-jobs: $SYNC_OUT" >> "$LOG"
fi

# Close this Terminal window after completion
osascript -e 'tell application "Terminal" to close (every window whose name contains "dotfiles-pull")' &>/dev/null &
