---
name: peekaboo
description: macOS GUI automation via Peekaboo. Use when asked to interact with the Mac Mini desktop, click buttons, type text, take screenshots, control windows/apps, navigate menus, or perform any visual UI automation.
allowed-tools: Bash(peekaboo:*)
metadata: {"openclaw":{"emoji":"P","requires":{"bins":["peekaboo"]}}}
---

# Peekaboo - macOS GUI Automation

Control the Mac Mini desktop via the `peekaboo` CLI (v3.0.0-beta3). Provides screenshot capture, UI element detection, clicking, typing, window/app management, menu interaction, and AI-powered multi-step automation.

## CRITICAL: GUI Session Requirement

Peekaboo requires Screen Recording and Accessibility TCC permissions which **only work from the macOS GUI (Aqua) session**. Running peekaboo directly over SSH will fail silently or error out.

**All peekaboo commands must use the `.command` file pattern** to execute in the GUI session:

### Helper Pattern for SSH Execution

```bash
# Write the command to a .command file and execute via `open`
ssh dylans-mac-mini 'cat > /tmp/peekaboo_op.command << '\''SCRIPT'\''
#!/bin/bash
export PATH=/opt/homebrew/bin:$PATH
peekaboo image --path /tmp/screenshot.png --json > /tmp/peekaboo_result.txt 2>&1
echo $? > /tmp/peekaboo_exit.txt
osascript -e '\''tell application "Terminal" to close (every window whose name contains "peekaboo_op")'\'' &>/dev/null &
SCRIPT
chmod +x /tmp/peekaboo_op.command && open /tmp/peekaboo_op.command'
sleep 3
ssh dylans-mac-mini "cat /tmp/peekaboo_exit.txt && cat /tmp/peekaboo_result.txt"
```

For simpler one-off commands, use this condensed pattern:

```bash
ssh dylans-mac-mini "echo '#!/bin/bash
export PATH=/opt/homebrew/bin:\$PATH
peekaboo YOUR_COMMAND_HERE > /tmp/peekaboo_result.txt 2>&1
echo \$? > /tmp/peekaboo_exit.txt
osascript -e \"tell application \\\"Terminal\\\" to close (every window whose name contains \\\"pk_op\\\")\" &>/dev/null &' > /tmp/pk_op.command && chmod +x /tmp/pk_op.command && open /tmp/pk_op.command"
sleep 3
ssh dylans-mac-mini "cat /tmp/peekaboo_exit.txt; cat /tmp/peekaboo_result.txt"
```

## Core Workflow: See Then Act

The standard automation pattern is:

1. **`peekaboo see`** - Capture and analyze UI elements, get element IDs
2. **`peekaboo click --on <ID>`** - Click on a discovered element
3. **`peekaboo type "text"`** - Type into the focused element

Element IDs from `see` (e.g., B1, T2, S1) are used by `click`, `drag`, `scroll`, and other interaction commands.

## Screenshots

```bash
# Capture the frontmost window
peekaboo image --path /tmp/screenshot.png

# Capture a specific app's window
peekaboo image --app Safari --path /tmp/safari.png

# Capture entire screen
peekaboo image --mode screen --path /tmp/screen.png

# Capture at Retina resolution
peekaboo image --retina --path /tmp/retina.png

# Capture and analyze with AI
peekaboo image --analyze "What is shown on screen?"
```

## Vision / UI Element Detection

```bash
# Analyze frontmost window, get element IDs
peekaboo see --json

# Analyze with annotated screenshot saved
peekaboo see --annotate --path /tmp/see.png --json

# Analyze a specific app
peekaboo see --app Safari --json

# Analyze a specific window
peekaboo see --app Safari --window-title "Login" --json

# Capture and ask AI about what's visible
peekaboo see --analyze "What buttons are visible?"
```

## Click

```bash
# Click on an element ID from `see`
peekaboo click --on B1

# Click by text query
peekaboo click "Submit"

# Click at specific coordinates
peekaboo click --coords 500,300

# Double-click
peekaboo click --on B1 --double

# Right-click
peekaboo click --on B1 --right

# Click in a specific app
peekaboo click --on T2 --app Safari
```

## Type Text

```bash
# Type text (human-like cadence by default)
peekaboo type "Hello World"

# Type and press return
peekaboo type "search query" --return

# Clear field first, then type
peekaboo type "new value" --clear

# Type at maximum speed
peekaboo type "fast text" --delay 0

# Press tab 3 times
peekaboo type --tab 3

# Type into a specific app
peekaboo type "text" --app "TextEdit"
```

## Keyboard Shortcuts (Hotkey)

```bash
# Copy
peekaboo hotkey "cmd,c"

# Paste
peekaboo hotkey "cmd,v"

# Select all
peekaboo hotkey "cmd,a"

# Open Spotlight
peekaboo hotkey "cmd,space"

# Reopen closed tab
peekaboo hotkey "cmd,shift,t"

# Target a specific app
peekaboo hotkey "cmd,s" --app "TextEdit"
```

## Scroll

```bash
# Scroll down 5 ticks
peekaboo scroll --direction down --amount 5

# Smooth scroll up
peekaboo scroll --direction up --amount 10 --smooth

# Scroll on a specific element
peekaboo scroll --direction down --amount 3 --on element_42
```

## Drag and Drop

```bash
# Drag element to element
peekaboo drag --from B1 --to T2

# Drag by coordinates
peekaboo drag --from-coords "100,200" --to-coords "400,300"

# Drag to an app (e.g., Trash)
peekaboo drag --from B1 --to-app Trash
```

## App Control

```bash
# List running apps
peekaboo app list --json

# Launch an app
peekaboo app launch "Safari"
peekaboo app launch "Safari" --open https://example.com

# Quit an app
peekaboo app quit --app Safari

# Quit all except certain apps
peekaboo app quit --all --except "Finder,Terminal"

# Switch to an app
peekaboo app switch --to Terminal

# Hide / unhide
peekaboo app hide --app Slack
peekaboo app unhide --app Slack

# Relaunch
peekaboo app relaunch Safari
```

## Window Management

```bash
# List windows for an app
peekaboo window list --app Safari --json

# Focus a window
peekaboo window focus --app "Visual Studio Code"
peekaboo window focus --app Safari --window-title "GitHub"

# Move a window
peekaboo window move --app TextEdit --x 100 --y 100

# Resize a window
peekaboo window resize --app Safari --width 1200 --height 800

# Set position and size together
peekaboo window set-bounds --app Chrome --x 50 --y 50 --width 1024 --height 768

# Minimize / maximize
peekaboo window minimize --app Finder
peekaboo window maximize --app Terminal

# Close a window
peekaboo window close --app Safari --window-title "GitHub"
```

## Menu Bar

```bash
# List all menu items for an app
peekaboo menu list --app Finder --json

# Click a menu item
peekaboo menu click --app Safari --item "New Window"

# Navigate nested menus
peekaboo menu click --app TextEdit --path "Format > Font > Show Fonts"

# Click system menu extras (WiFi, Bluetooth, etc.)
peekaboo menu click-extra --title "WiFi"
```

## System Info

```bash
# List running apps
peekaboo list apps --json

# List windows
peekaboo list windows --app Safari --json

# List screens/displays
peekaboo list screens --json

# List menu bar status items
peekaboo list menubar --json

# Check permissions
peekaboo list permissions
```

## Clipboard

```bash
# Read clipboard
peekaboo clipboard read

# Write to clipboard
peekaboo clipboard write "some text"
```

## AI Agent (Multi-Step Automation)

For complex tasks, use the built-in agent which plans and executes multiple steps autonomously:

```bash
# Run a multi-step task
peekaboo agent "Open Safari, navigate to example.com, and take a screenshot"

# Dry run to see planned steps
peekaboo agent "Prepare the TestFlight build" --dry-run

# Limit steps
peekaboo agent "Fill out the form" --max-steps 10

# Choose AI model
peekaboo agent "Describe the screen" --model claude-opus-4-5
```

## Global Flags

All commands support these flags:

| Flag | Description |
|------|-------------|
| `--json` / `-j` | Machine-readable JSON output |
| `--verbose` / `-v` | Enable verbose logging |
| `--no-remote` | Force local execution, skip remote bridge hosts |

## Notes

- Always use `--json` when parsing output programmatically
- Element IDs from `see` are ephemeral -- they change between captures. Always run `see` immediately before using IDs.
- The `see` + `click`/`type` pattern is the fundamental workflow. Do not guess coordinates; use element IDs.
- `peekaboo agent` calls external AI APIs and may incur costs. Prefer manual see/click/type sequences for predictable operations.
- Binary is at `/opt/homebrew/bin/peekaboo` on the Mac Mini.
- Permissions check: `peekaboo list permissions` (but must run from GUI session).

## Troubleshooting

- **Commands fail or return empty over SSH**: You forgot the `.command` file pattern. Peekaboo needs the GUI session.
- **Element IDs not found**: The snapshot expired. Run `peekaboo see` again to get fresh IDs.
- **Click hits wrong target**: Use `--coords` with exact coordinates, or re-run `see --annotate` to verify element positions visually.
- **`see` returns no elements**: The app window may be minimized or behind other windows. Use `peekaboo window focus --app <name>` first.
- **Typing goes to wrong field**: Use `peekaboo click` to focus the target input field before `peekaboo type`.
