#!/usr/bin/env bash
# git-sync.sh — Commit and push changes for OpenClaw repos
#
# Usage:
#   git-sync.sh dotfiles "commit message"   — Commit & push dotfiles changes
#   git-sync.sh workspace "commit message"  — Commit & push workspace changes
#   git-sync.sh both "commit message"       — Commit & push both repos
#
# Designed for OpenClaw to call after modifying files.
# Requires: gh CLI authenticated (for HTTPS push)

set -euo pipefail

DOTFILES_REPO="$HOME/dotfiles"
WORKSPACE_REPO="$HOME/.openclaw/workspace"
LOG="$HOME/.openclaw/logs/git-sync.log"
GH="$HOME/.local/bin/gh"

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"
  echo "$*"
}

sync_repo() {
  local repo="$1"
  local msg="$2"
  local name
  name=$(basename "$repo")

  cd "$repo" || { log "ERROR: Cannot cd to $repo"; return 1; }

  local changes
  changes=$(git status --porcelain)

  if [ -z "$changes" ]; then
    log "$name: No changes to commit"
    return 0
  fi

  # Pull first to avoid conflicts
  git pull --ff-only origin main 2>/dev/null || true

  # Stage all changes
  git add -A
  git commit -m "$msg" || { log "$name: Commit failed"; return 1; }
  log "$name: Committed — $msg"

  # Push
  if git push origin main 2>&1; then
    log "$name: Pushed successfully"
  else
    log "$name: Push failed (will retry on next sync)"
    return 1
  fi
}

usage() {
  echo "Usage: $0 {dotfiles|workspace|both} \"commit message\"" >&2
  exit 1
}

[ $# -ge 2 ] || usage

TARGET="$1"
shift
MSG="$*"

# Ensure gh credential helper is available
export PATH="$HOME/.local/bin:$PATH"

case "$TARGET" in
  dotfiles)
    sync_repo "$DOTFILES_REPO" "$MSG"
    ;;
  workspace)
    sync_repo "$WORKSPACE_REPO" "$MSG"
    ;;
  both)
    sync_repo "$DOTFILES_REPO" "$MSG"
    sync_repo "$WORKSPACE_REPO" "$MSG"
    ;;
  *)
    usage
    ;;
esac
