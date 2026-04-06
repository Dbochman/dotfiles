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
    # Preserve executable bit on CLI wrappers inside skills
    find "$SKILLS_DST/$skill_name" -maxdepth 1 -type f ! -name "*.md" ! -name "*.json" ! -name "*.yaml" -exec chmod +x {} +
    DEPLOYED=$((DEPLOYED + 1))
  done
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skills: deployed $DEPLOYED skills to $SKILLS_DST" >> "$LOG"
fi

# Deploy CLI wrappers to ~/.openclaw/bin/
BIN_SRC="$REPO/openclaw/bin"
BIN_DST="$HOME/.openclaw/bin"
WRAPPER_DEPLOYED=0
for wrapper in cielo roomba crosstown-roomba 8sleep mysa petlibro litter-robot crisismode ring; do
  if [ -f "$BIN_SRC/$wrapper" ]; then
    cp "$BIN_SRC/$wrapper" "$BIN_DST/$wrapper"
    chmod +x "$BIN_DST/$wrapper"
    WRAPPER_DEPLOYED=$((WRAPPER_DEPLOYED + 1))
  fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: deployed $WRAPPER_DEPLOYED to $BIN_DST" >> "$LOG"

# Deploy dashboard scripts to ~/.openclaw/bin/
DASHBOARDS_DEPLOYED=0
for dashboard in nest-dashboard.py usage-dashboard.py home-dashboard.py; do
  if [ -f "$BIN_SRC/$dashboard" ]; then
    cp "$BIN_SRC/$dashboard" "$BIN_DST/$dashboard"
    chmod +x "$BIN_DST/$dashboard"
    DASHBOARDS_DEPLOYED=$((DASHBOARDS_DEPLOYED + 1))
  fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) dashboards: deployed $DASHBOARDS_DEPLOYED to $BIN_DST" >> "$LOG"

# Smoke test — verify CLIs resolve on PATH
export PATH="$BIN_DST:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
SMOKE_FAIL=0
for cmd in cielo roomba crosstown-roomba 8sleep mysa petlibro litter-robot crisismode ring nest hue speaker; do
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

# Deploy CrisisMode config and check plugins
CRISISMODE_SRC="$REPO/openclaw/crisismode"
CRISISMODE_DST="$HOME/.crisismode"
if [ -d "$CRISISMODE_SRC" ]; then
  mkdir -p "$CRISISMODE_DST"
  cp "$CRISISMODE_SRC/crisismode.yaml" "$CRISISMODE_DST/crisismode.yaml"
  # Deploy custom check plugins
  if [ -d "$CRISISMODE_SRC/checks" ]; then
    mkdir -p "$CRISISMODE_DST/checks"
    CHECKS_DEPLOYED=0
    for check_dir in "$CRISISMODE_SRC/checks"/*/; do
      check_name=$(basename "$check_dir")
      rm -rf "$CRISISMODE_DST/checks/$check_name"
      cp -R "$check_dir" "$CRISISMODE_DST/checks/$check_name"
      chmod +x "$CRISISMODE_DST/checks/$check_name/check.sh" 2>/dev/null
      CHECKS_DEPLOYED=$((CHECKS_DEPLOYED + 1))
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) crisismode: deployed config + $CHECKS_DEPLOYED check plugins" >> "$LOG"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) crisismode: deployed config to $CRISISMODE_DST" >> "$LOG"
  fi
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
