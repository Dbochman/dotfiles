#!/bin/bash
# dotfiles-pull.command — Auto-pull dotfiles repo and deploy skills
# Runs as a LaunchAgent daily via Terminal.app (for git credential access)
# Skills are real copies (not symlinks) because OpenClaw v2026.3.7+ rejects
# symlinks whose realPath resolves outside the configured rootDir.

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

# Deploy skills as real copies (OpenClaw rejects symlinks via realPath check)
SKILLS_SRC="$REPO/openclaw/skills"
SKILLS_DST="$HOME/.openclaw/skills"
if [ -d "$SKILLS_SRC" ]; then
  DEPLOYED=0
  for skill_dir in "$SKILLS_SRC"/*/; do
    skill_name=$(basename "$skill_dir")
    rm -rf "$SKILLS_DST/$skill_name"
    cp -R "$skill_dir" "$SKILLS_DST/$skill_name"
    # Remove any nested symlinks that snuck in
    find "$SKILLS_DST/$skill_name" -type l -delete 2>/dev/null
    DEPLOYED=$((DEPLOYED + 1))
  done
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skills: deployed $DEPLOYED skills to $SKILLS_DST" >> "$LOG"
fi

# Deploy CLI wrappers to ~/.openclaw/bin/
BIN_SRC="$REPO/openclaw/bin"
BIN_DST="$HOME/.openclaw/bin"
WRAPPER_DEPLOYED=0
for wrapper in cielo roomba crosstown-roomba 8sleep mysa petlibro litter-robot; do
  if [ -f "$BIN_SRC/$wrapper" ]; then
    cp "$BIN_SRC/$wrapper" "$BIN_DST/$wrapper"
    chmod +x "$BIN_DST/$wrapper"
    WRAPPER_DEPLOYED=$((WRAPPER_DEPLOYED + 1))
  fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: deployed $WRAPPER_DEPLOYED to $BIN_DST" >> "$LOG"

# Smoke test — verify CLIs resolve on PATH
export PATH="$BIN_DST:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
SMOKE_FAIL=0
for cmd in cielo roomba crosstown-roomba 8sleep mysa petlibro litter-robot nest hue speaker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $cmd not on PATH" >> "$LOG"
    SMOKE_FAIL=$((SMOKE_FAIL + 1))
  fi
done
if [ $SMOKE_FAIL -gt 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: smoke test FAILED ($SMOKE_FAIL missing)" >> "$LOG"
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: smoke test PASSED" >> "$LOG"
fi

# Deploy workspace files (SOUL.md, TOOLS.md, etc.)
WORKSPACE_SRC="$REPO/openclaw/workspace"
WORKSPACE_DST="$HOME/.openclaw/workspace"
if [ -d "$WORKSPACE_SRC" ] && [ -d "$WORKSPACE_DST" ]; then
  for f in TOOLS.md HEARTBEAT.md; do
    if [ -f "$WORKSPACE_SRC/$f" ]; then
      cp "$WORKSPACE_SRC/$f" "$WORKSPACE_DST/$f"
    fi
  done
  # SOUL.md has real values on Mini (not placeholders) — don't overwrite
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed TOOLS.md, HEARTBEAT.md" >> "$LOG"
fi

# Deploy updated cron job definitions (preserves runtime state)
if [ -x "$REPO/openclaw/sync-cron-jobs.sh" ]; then
  SYNC_OUT=$("$REPO/openclaw/sync-cron-jobs.sh" deploy 2>&1)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sync-cron-jobs: $SYNC_OUT" >> "$LOG"
fi

# Close this Terminal window after completion
osascript -e 'tell application "Terminal" to close (every window whose name contains "dotfiles-pull")' &>/dev/null &
