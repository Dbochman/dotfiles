---
name: shortcuts
description: Run Apple Shortcuts for HomeKit scenes, automations, and system actions. Use when asked about home scenes, automations, fireplace, or triggering shortcuts.
allowed-tools: Bash(shortcuts:*)
metadata: {"openclaw":{"emoji":"S"}}
---

# Apple Shortcuts

Run macOS Shortcuts via the `shortcuts` CLI.

## Available Commands

### List all shortcuts
```bash
shortcuts list
```

### Run a shortcut
```bash
shortcuts run "<shortcut name>"
```

### Run with input
```bash
echo "input text" | shortcuts run "<shortcut name>"
```

## Available Shortcuts

- **Fire on** — Turn on the fireplace
- **Fire off** — Turn off the fireplace
- **Fireplace on** — Turn on the fireplace (alternate)
- **Fireplace off** — Turn off the fireplace (alternate)
- **Shazam shortcut** — Identify currently playing music
- **Take a Break** — Break timer
- **Text Last Image** — Send the last photo via text

## Notes

- Shortcut names are case-sensitive and must match exactly
- Some shortcuts require HomeKit devices to be reachable
- For fireplace control, prefer "Fire on" / "Fire off"
- Always confirm the action was triggered by telling the user what you ran
