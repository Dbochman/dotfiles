# Dotfiles

Personal dotfiles managed with git and symlinks.

## Install on new machine

```bash
git clone git@github.com:Dbochman/dotfiles.git ~/dotfiles
~/dotfiles/install.sh
```

## What's included

- `.zshrc` - Shell config with PATH and aliases
- `.gitconfig` - Git user info, LFS, sensible defaults
- `.codex/config.toml` - Codex model and feature settings
- `.local/bin/codex-quick` - Fast Codex CLI wrapper (medium reasoning)

## Adding new dotfiles

1. Copy the file to `~/dotfiles/`
2. Add a symlink line to `install.sh`
3. Commit and push
