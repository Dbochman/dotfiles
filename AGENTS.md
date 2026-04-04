# Repository Guidelines

## Project Structure & Module Organization

This repo is a macOS dotfiles and automation workspace, not a packaged app. Root files such as [`install.sh`](/Users/dbochman/repos/dotfiles/install.sh), [`sync.sh`](/Users/dbochman/repos/dotfiles/sync.sh), [`Brewfile`](/Users/dbochman/repos/dotfiles/Brewfile), [`zshrc`](/Users/dbochman/repos/dotfiles/zshrc), [`gitconfig`](/Users/dbochman/repos/dotfiles/gitconfig), and [`ssh_config`](/Users/dbochman/repos/dotfiles/ssh_config) define the base machine setup. User-facing CLIs live in [`bin/`](/Users/dbochman/repos/dotfiles/bin). Claude/Codex configuration lives under [`.claude/`](/Users/dbochman/repos/dotfiles/.claude) and [`.codex/`](/Users/dbochman/repos/dotfiles/.codex). OpenClaw automation, dashboards, LaunchAgents, skills, and operational docs live under [`openclaw/`](/Users/dbochman/repos/dotfiles/openclaw). Reference material belongs in [`docs/`](/Users/dbochman/repos/dotfiles/docs).

## Build, Test, and Development Commands

Use the repo’s scripts directly:

- `./install.sh --dry-run` previews symlink and backup changes for a new machine or config update.
- `./install.sh` applies links and setup.
- `./sync.sh` shows local vs. repo-managed Claude items.
- `./sync.sh validate` verifies managed skills, hooks, and commands.
- `./sync.sh pull` refreshes from Git and reruns install.
- `brew bundle --file=~/dotfiles/Brewfile` installs Homebrew dependencies.
- `plutil -lint openclaw/launchagents/*.plist` is the quickest sanity check after editing LaunchAgents.

## Coding Style & Naming Conventions

Bash and Python are the primary languages. Use `#!/usr/bin/env bash` or `python3`, keep Bash portable on macOS, prefer quoted variables, and follow the existing function-first script style with `set -o pipefail` where appropriate. Python uses 4-space indentation and standard-library-first scripts. Name executable tools with lowercase, CLI-style names (`bin/websearch`, `openclaw/bin/usage-snapshot.sh`). Keep Markdown docs descriptive; subsystem guides in `openclaw/` often use uppercase filenames.

## Testing Guidelines

There is no single automated test suite. Validate changes with dry runs, targeted script smoke tests, and syntax checks close to the files you touched. For install/sync changes, run `./install.sh --dry-run` and `./sync.sh validate`. For LaunchAgents or plist edits, use `plutil -lint`. Document manual verification steps in your PR when behavior is operational rather than unit-tested.

## Commit & Pull Request Guidelines

Follow the existing Conventional Commit style: `feat:`, `fix:`, `docs:`, `refactor:`, with optional scopes like `fix(skills): ...`. Keep commits focused and deployment-aware. PRs should state what changed, affected paths, any machine or secret prerequisites, and the commands used to verify the change. Include screenshots only for dashboard or UI-facing updates.

## Security & Local Overrides

Never commit secrets. Credentials are expected through 1Password/`op`, and machine-local overrides belong in ignored paths such as `.local/`, `.backup/`, `.claude/settings.local.json`, or `openclaw/skills/**/*.local.md`. If you modify [`openclaw/workspace/`](/Users/dbochman/repos/dotfiles/openclaw/workspace), follow its nested [`AGENTS.md`](/Users/dbochman/repos/dotfiles/openclaw/workspace/AGENTS.md) as the tighter local contract.
