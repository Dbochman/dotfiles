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

# Link config directories
mkdir -p "$HOME/.codex"
link_file "$DOTFILES_DIR/.codex/config.toml" "$HOME/.codex/config.toml"

# Link scripts
mkdir -p "$HOME/.local/bin"
for script in "$DOTFILES_DIR/.local/bin"/*; do
  [ -f "$script" ] && link_file "$script" "$HOME/.local/bin/$(basename "$script")"
done

echo ""
echo "Done! Restart your shell or run: source ~/.zshrc"
