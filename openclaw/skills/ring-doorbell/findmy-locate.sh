#!/bin/bash
# findmy-locate.sh — Navigate to a person in FindMy and capture their zoomed location
#
# Usage: findmy-locate.sh <name>
# Example: findmy-locate.sh Dylan
#          findmy-locate.sh Julia
#
# Uses keyboard navigation (arrow keys) to select people in the sidebar.
# FindMy blocks programmatic mouse clicks but accepts keyboard input via Peekaboo.
#
# People order in sidebar: Me (0) → Julia Jennings (1) → Dylan Bochman (2)
#
# Requires: peekaboo with Screen Recording + Accessibility permissions
# Must run from LaunchAgent context (not SSH)

set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

NAME="${1:-}"
if [ -z "$NAME" ]; then
    echo '{"error": "Usage: findmy-locate.sh <name>"}'
    exit 1
fi

CAPTURE_DIR="$HOME/.openclaw/ring-listener/findmy"
mkdir -p "$CAPTURE_DIR"

# People sidebar order (0-indexed from top)
# Me (clawdbotbochman): position 0
# Julia Jennings:       position 1
# Dylan Bochman:        position 2

case "$(echo "$NAME" | tr '[:upper:]' '[:lower:]')" in
    dylan|dylan\ bochman|db)
        TARGET_POS=2
        PERSON="Dylan Bochman"
        ;;
    julia|julia\ jennings|jj)
        TARGET_POS=1
        PERSON="Julia Jennings"
        ;;
    me|clawdbot)
        TARGET_POS=0
        PERSON="Me (clawdbotbochman)"
        ;;
    *)
        echo "{\"error\": \"Unknown person: $NAME. Use Dylan, Julia, or Me\"}"
        exit 1
        ;;
esac

# Activate FindMy
open -a FindMy
sleep 1

# Navigate to top of list first (press Up enough times to reach "Me")
for i in 1 2 3; do
    peekaboo press up --app "Find My" >/dev/null 2>&1
    sleep 0.3
done

# Navigate down to target position
for i in $(seq 1 "$TARGET_POS"); do
    peekaboo press down --app "Find My" >/dev/null 2>&1
    sleep 0.5
done

# Wait for map animation
sleep 3

# Capture the zoomed view
CAPTURE_PATH="$CAPTURE_DIR/findmy-locate-$(date +%s).png"
peekaboo see --app "Find My" --path "$CAPTURE_PATH" >/dev/null 2>&1

# Check for Desktop fallback (peekaboo see sometimes saves there)
if [ ! -f "$CAPTURE_PATH" ] || [ "$(stat -f%z "$CAPTURE_PATH" 2>/dev/null || echo 0)" -lt 50000 ]; then
    DESKTOP_FILE=$(ls -t "$HOME/Desktop/peekaboo_see_"*.png 2>/dev/null | head -1)
    if [ -n "$DESKTOP_FILE" ] && [ "$(stat -f%z "$DESKTOP_FILE")" -gt 50000 ]; then
        mv "$DESKTOP_FILE" "$CAPTURE_PATH"
    fi
fi

if [ -f "$CAPTURE_PATH" ] && [ "$(stat -f%z "$CAPTURE_PATH")" -gt 50000 ]; then
    echo "{\"success\": true, \"person\": \"$PERSON\", \"capture\": \"$CAPTURE_PATH\", \"size\": $(stat -f%z "$CAPTURE_PATH")}"
else
    echo "{\"error\": \"Failed to capture FindMy for $PERSON\", \"person\": \"$PERSON\"}"
    exit 1
fi
