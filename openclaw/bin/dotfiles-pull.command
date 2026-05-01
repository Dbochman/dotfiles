#!/bin/bash
# dotfiles-pull.command — Auto-pull dotfiles repo and deploy skills
# Runs as a LaunchAgent daily via Terminal.app (for git credential access)
# Skills are real copies (not symlinks) because OpenClaw v2026.3.7+ rejects
# symlinks whose realPath resolves outside the configured rootDir.

LOG="$HOME/.openclaw/logs/dotfiles-pull.log"
REPO="$HOME/dotfiles"

set -euo pipefail
trap 'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FATAL: dotfiles-pull failed at line $LINENO" >> "$LOG"' ERR

cd "$REPO" || exit 1

DIRTY=$(git status --porcelain)

# Git stash operations can fail benignly — disable strict error mode for this section
set +e
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
set -e

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

# Deploy CLI wrappers and scripts to ~/.openclaw/bin/
BIN_SRC="$REPO/openclaw/bin"
BIN_DST="$HOME/.openclaw/bin"
WRAPPER_DEPLOYED=0
DEPLOYED_WRAPPERS=""
for wrapper in "$BIN_SRC"/*; do
  [ -f "$wrapper" ] || continue
  fname=$(basename "$wrapper")
  # Skip files with extensions (deployed separately or not wrappers) and non-executables
  case "$fname" in
    *.py|*.sh|*.command|*.md|*.json|*.yaml) continue ;;
  esac
  [ -x "$wrapper" ] || continue
  cp "$wrapper" "$BIN_DST/$fname"
  chmod +x "$BIN_DST/$fname"
  WRAPPER_DEPLOYED=$((WRAPPER_DEPLOYED + 1))
  DEPLOYED_WRAPPERS="$DEPLOYED_WRAPPERS $fname"
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: deployed $WRAPPER_DEPLOYED to $BIN_DST" >> "$LOG"

# Deploy dashboard and utility scripts to ~/.openclaw/bin/
SCRIPTS_DEPLOYED=0
for script in "$BIN_SRC"/*.py "$BIN_SRC"/*.sh; do
  [ -f "$script" ] || continue
  fname=$(basename "$script")
  cp "$script" "$BIN_DST/$fname"
  chmod +x "$BIN_DST/$fname"
  SCRIPTS_DEPLOYED=$((SCRIPTS_DEPLOYED + 1))
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) scripts: deployed $SCRIPTS_DEPLOYED to $BIN_DST" >> "$LOG"

# Symlink top-level bin/ scripts into /opt/homebrew/bin/ so they track dotfiles HEAD.
# Matches the pattern set by install.sh for hue/nest/speaker — replaces any stale
# regular-file copies so a fix committed to bin/<cli> propagates on the next pull.
TOP_BIN_SRC="$REPO/bin"
TOP_BIN_DST="/opt/homebrew/bin"
TOP_BIN_LINKED=0
if [ -d "$TOP_BIN_SRC" ]; then
  for script in "$TOP_BIN_SRC"/*; do
    [ -f "$script" ] || continue
    [ -x "$script" ] || continue
    fname=$(basename "$script")
    # Skip if already pointing at the right place
    if [ -L "$TOP_BIN_DST/$fname" ] && [ "$(readlink "$TOP_BIN_DST/$fname")" = "$script" ]; then
      continue
    fi
    ln -sfn "$script" "$TOP_BIN_DST/$fname"
    TOP_BIN_LINKED=$((TOP_BIN_LINKED + 1))
  done
fi
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) top-bin: linked $TOP_BIN_LINKED to $TOP_BIN_DST" >> "$LOG"

# Smoke test — verify deployed wrappers resolve on PATH
export PATH="$BIN_DST:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
SMOKE_FAIL=0
for cmd in $DEPLOYED_WRAPPERS; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $cmd not on PATH" >> "$LOG"
    SMOKE_FAIL=$((SMOKE_FAIL + 1))
  fi
done
# Also check external CLIs expected on PATH
for cmd in hue speaker goplaces; do
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
      # Remove symlinks first — cp fails if dst is a symlink to src
      [ -L "$WORKSPACE_DST/$f" ] && rm -f "$WORKSPACE_DST/$f"
      cp "$WORKSPACE_SRC/$f" "$WORKSPACE_DST/$f"
    fi
  done
  # SOUL.md has real values on Mini (not placeholders) — don't overwrite
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed TOOLS.md, HEARTBEAT.md" >> "$LOG"

  # Deploy workspace scripts (presence-detect, grocery-reorder, etc.)
  SCRIPTS_SRC="$REPO/openclaw/workspace/scripts"
  SCRIPTS_DST="$WORKSPACE_DST/scripts"
  if [ -d "$SCRIPTS_SRC" ] && [ -d "$SCRIPTS_DST" ]; then
    WS_SCRIPTS_DEPLOYED=0
    for script in "$SCRIPTS_SRC"/*; do
      [ -f "$script" ] || continue
      fname=$(basename "$script")
      [ -L "$SCRIPTS_DST/$fname" ] && rm -f "$SCRIPTS_DST/$fname"
      cp "$script" "$SCRIPTS_DST/$fname"
      chmod +x "$SCRIPTS_DST/$fname"
      WS_SCRIPTS_DEPLOYED=$((WS_SCRIPTS_DEPLOYED + 1))
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed $WS_SCRIPTS_DEPLOYED scripts to $SCRIPTS_DST" >> "$LOG"
  fi
fi

# Sync files the Crosstown MBP runs (it has no dotfiles auto-pull of its own;
# without this, scripts on MBP go stale relative to dotfiles). The Mini owns
# the dedicated SSH key, so existence of the key implies we're on the Mini
# and the MBP is the intended target.
# Format: "<repo-relative-src>:<MBP-home-relative-dst>"
MBP_SSH_KEY="$HOME/.ssh/id_mini_to_mbp"
MBP_HOST="dylans-macbook-pro"
MBP_SYNC_PAIRS=(
  "openclaw/workspace/scripts/presence-detect.sh:.openclaw/workspace/scripts/presence-detect.sh"
  "openclaw/rest980/start-10max.sh:.openclaw/rest980/start-10max.sh"
  "openclaw/rest980/start-j5.sh:.openclaw/rest980/start-j5.sh"
  "openclaw/rest980/roomba-cmd.js:.openclaw/rest980/roomba-cmd.js"
)
if [ -f "$MBP_SSH_KEY" ]; then
  MBP_SYNC_OK=0
  MBP_SYNC_TOTAL=0
  MBP_SYNC_ERR=""
  for pair in "${MBP_SYNC_PAIRS[@]}"; do
    src_rel="${pair%%:*}"
    dst_rel="${pair##*:}"
    src="$REPO/$src_rel"
    [ -f "$src" ] || continue
    MBP_SYNC_TOTAL=$((MBP_SYNC_TOTAL + 1))
    if scp_err=$(scp -i "$MBP_SSH_KEY" -o IdentityAgent=none \
                     -o StrictHostKeyChecking=accept-new \
                     -o ConnectTimeout=10 -q \
                     "$src" "$MBP_HOST:$dst_rel" 2>&1); then
      MBP_SYNC_OK=$((MBP_SYNC_OK + 1))
    else
      MBP_SYNC_ERR="$scp_err"
    fi
  done
  if [ "$MBP_SYNC_OK" -eq "$MBP_SYNC_TOTAL" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: synced $MBP_SYNC_OK/$MBP_SYNC_TOTAL files to $MBP_HOST" >> "$LOG"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: WARN synced $MBP_SYNC_OK/$MBP_SYNC_TOTAL files to $MBP_HOST: ${MBP_SYNC_ERR:-unknown error}" >> "$LOG"
  fi
fi

# Deploy updated cron job definitions (preserves runtime state)
if [ -x "$REPO/openclaw/sync-cron-jobs.sh" ]; then
  SYNC_OUT=$("$REPO/openclaw/sync-cron-jobs.sh" deploy 2>&1)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sync-cron-jobs: $SYNC_OUT" >> "$LOG"
fi

# Self-update: keep the deployed copy of this script in sync with repo HEAD.
# Without this, fixes to dotfiles-pull.command itself (and anything its later
# blocks deploy) never reach the Mini — launchd runs the DEPLOYED copy, and
# the wrapper-deploy loop above skips *.command. Use cp+mv for atomic replace
# so the still-running bash process keeps reading the old inode.
SELF_SRC="$REPO/openclaw/bin/dotfiles-pull.command"
SELF_DST="$HOME/.openclaw/bin/dotfiles-pull.command"
if [ -f "$SELF_SRC" ] && ! cmp -s "$SELF_SRC" "$SELF_DST"; then
  cp "$SELF_SRC" "$SELF_DST.new"
  chmod +x "$SELF_DST.new"
  mv "$SELF_DST.new" "$SELF_DST"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) self: updated $SELF_DST from repo" >> "$LOG"
fi

# Close this Terminal window after completion
osascript -e 'tell application "Terminal" to close (every window whose name contains "dotfiles-pull")' &>/dev/null &
