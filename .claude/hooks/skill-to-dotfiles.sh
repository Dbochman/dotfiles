#!/bin/bash
# PostToolUse hook: When a file is written under ~/.claude/skills/,
# move the skill directory to dotfiles and create a symlink back.
#
# Receives JSON on stdin with tool_input.file_path

DOTFILES_SKILLS="$HOME/repos/dotfiles/.claude/skills"

# Read stdin
INPUT=$(cat)

# Extract the file path that was written
FILE_PATH=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

# Only act on files under ~/.claude/skills/
case "$FILE_PATH" in
  "$HOME/.claude/skills/"*)
    ;;
  *)
    exit 0
    ;;
esac

# Extract skill directory name (first component after ~/.claude/skills/)
SKILL_NAME=$(echo "$FILE_PATH" | sed "s|$HOME/.claude/skills/||" | cut -d/ -f1)

if [ -z "$SKILL_NAME" ]; then
  exit 0
fi

SKILL_SRC="$HOME/.claude/skills/$SKILL_NAME"
SKILL_DST="$DOTFILES_SKILLS/$SKILL_NAME"

# Skip if already a symlink (already tracked)
if [ -L "$SKILL_SRC" ]; then
  # But the file was written to the symlink target (dotfiles), so nothing to do
  exit 0
fi

# Skip if dotfiles repo doesn't exist
if [ ! -d "$DOTFILES_SKILLS" ]; then
  exit 0
fi

# Copy to dotfiles
cp -r "$SKILL_SRC" "$SKILL_DST"

# Replace with symlink
rm -rf "$SKILL_SRC"
ln -s "$SKILL_DST/" "$SKILL_SRC"
