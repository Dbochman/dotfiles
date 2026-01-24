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

## Adding new dotfiles

1. Copy the file to `~/dotfiles/`
2. Add a symlink line to `install.sh`
3. Commit and push
