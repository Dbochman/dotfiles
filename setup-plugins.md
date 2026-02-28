# Plugin Setup

After running `install.sh`, open Claude Code and run these commands to install plugins:

## Required Plugins

```
/plugin marketplace add jarrodwatts/claude-hud
/plugin install claude-hud
/claude-hud:setup

```

## Additional Plugins

```
/plugin marketplace add anthropics/claude-plugins-official
/plugin install code-simplifier

/plugin marketplace add jarrodwatts/claude-delegator
/plugin install claude-delegator
```

## Configure Claude HUD

After installation, configure with:
```
/claude-hud:configure
```

Recommended preset: **Essential** (activity lines with minimal clutter)

## What's Tracked in Dotfiles

The following are already symlinked by `install.sh`:
- `~/.claude/settings.json` — plugin enablement, status line, hooks, env
- `~/.claude/plugins/known_marketplaces.json` — marketplace registry

After running `install.sh`, the marketplaces and settings are pre-configured.
You just need to run the `/plugin install` commands above to download the actual plugin code.
