# Dotfiles

Personal dotfiles managed with git and symlinks.

## Install on new machine

```bash
git clone git@github.com:Dbochman/dotfiles.git ~/dotfiles
~/dotfiles/install.sh
```

## What's included

### Shell & Git
- `.zshrc` - Shell config with PATH and aliases
- `.gitconfig` - Git user info, LFS, sensible defaults

### Codex CLI
- `.codex/config.toml` - Codex model and feature settings
- `.local/bin/codex-quick` - Fast Codex CLI wrapper (medium reasoning)

### Claude Code
- `.claude/CLAUDE.md` - Global instructions for all projects
- `.claude/preferences.md` - User preferences and working style
- `.claude/settings.json` - Hooks config, enabled plugins
- `.claude/skills/` - 14 custom skills (debugging patterns, gotchas)
- `.claude/commands/` - Custom slash commands (rams, ui-skills, etc.)
- `.claude/hooks/` - Pre-tool hooks (no-rm safety, continuous learning)

### SSH & Secrets (via 1Password)
- `ssh_config` - Uses 1Password SSH Agent for all hosts
- `setup-1password.md` - Guide for SSH keys and environment variables

## SSH Keys

SSH keys are stored in 1Password and served via the SSH Agent. No keys on disk.

**Setup:**
1. Install 1Password desktop app
2. Settings → Developer → Enable "SSH Agent"
3. Settings → Developer → Enable "Integrate with 1Password CLI"
4. Run `install.sh` (links `~/.ssh/config`)

## API Keys & Secrets

Secrets are stored in 1Password and accessed via `op` CLI. Never in dotfiles.

**Install CLI:**
```bash
brew install 1password-cli
op signin
```

**Common references:**
```bash
# OpenAI
export OPENAI_API_KEY=$(op read "op://Private/OpenAI API Key/password")

# GitHub
export GITHUB_TOKEN=$(op read "op://Private/GitHub Personal Access Token/token")

# Codex
export CODEX_API_KEY=$(op read "op://Private/API Credentials - Codex/credential")
```

**Per-project secrets:** See `setup-1password.md` for direnv integration.

## Homebrew Packages

Install all packages from Brewfile:
```bash
brew bundle --file=~/dotfiles/Brewfile
```

To update Brewfile after installing new packages:
```bash
brew bundle dump --file=~/dotfiles/Brewfile --force
cd ~/dotfiles && git add Brewfile && git commit -m "Update Brewfile"
```

## Adding new dotfiles

1. Copy the file to `~/dotfiles/`
2. Add a symlink line to `install.sh`
3. Commit and push
