#!/bin/bash
# Dotfiles install script
# Run on new machines: git clone <repo> ~/dotfiles && ~/dotfiles/install.sh

set -e
DOTFILES_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing dotfiles from $DOTFILES_DIR"

# Backup existing files
backup_if_exists() {
  if [ -e "$1" ] && [ ! -L "$1" ]; then
    echo "  Backing up $1 to $1.backup"
    mv "$1" "$1.backup"
  fi
}

# Create symlink
link_file() {
  local src="$1"
  local dst="$2"
  backup_if_exists "$dst"

  # Create parent directory if needed
  mkdir -p "$(dirname "$dst")"

  # Remove existing symlink
  [ -L "$dst" ] && rm "$dst"

  ln -s "$src" "$dst"
  echo "  Linked $dst -> $src"
}

# Link dotfiles
link_file "$DOTFILES_DIR/zshrc" "$HOME/.zshrc"
link_file "$DOTFILES_DIR/gitconfig" "$HOME/.gitconfig"

# SSH config (uses 1Password SSH Agent)
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
link_file "$DOTFILES_DIR/ssh_config" "$HOME/.ssh/config"

# Link config directories
mkdir -p "$HOME/.codex"
link_file "$DOTFILES_DIR/.codex/config.toml" "$HOME/.codex/config.toml"

# Link Claude Code config
mkdir -p "$HOME/.claude"
link_file "$DOTFILES_DIR/.claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
link_file "$DOTFILES_DIR/.claude/preferences.md" "$HOME/.claude/preferences.md"
link_file "$DOTFILES_DIR/.claude/settings.json" "$HOME/.claude/settings.json"
link_file "$DOTFILES_DIR/.claude/session-notes.md" "$HOME/.claude/session-notes.md"
link_file "$DOTFILES_DIR/.claude/skills" "$HOME/.claude/skills"
link_file "$DOTFILES_DIR/.claude/commands" "$HOME/.claude/commands"
link_file "$DOTFILES_DIR/.claude/hooks" "$HOME/.claude/hooks"

# Link plugin marketplace references
mkdir -p "$HOME/.claude/plugins"
link_file "$DOTFILES_DIR/.claude/plugins/known_marketplaces.json" "$HOME/.claude/plugins/known_marketplaces.json"

# Link scripts
mkdir -p "$HOME/.local/bin"
for script in "$DOTFILES_DIR/.local/bin"/*; do
  [ -f "$script" ] && link_file "$script" "$HOME/.local/bin/$(basename "$script")"
done

echo ""
echo "Done! Restart your shell or run: source ~/.zshrc"
echo ""
echo "To install Claude Code plugins, see: ~/dotfiles/setup-plugins.md"
