#!/bin/bash
# findmy-locate — Navigate to a person in FindMy and capture their zoomed map location
#
# Usage: findmy-locate <name>
#   name: dylan, julia, both, or me
#
# Uses Peekaboo keyboard navigation to select people in the FindMy sidebar.
# FindMy blocks programmatic mouse clicks but accepts keyboard input via Peekaboo.
#
# People order in sidebar: Me (0) → Julia Jennings (1) → Dylan Bochman (2)
#
# Requires:
#   - peekaboo with Screen Recording + Accessibility TCC grants
#   - ~/Applications/Peekaboo.app (TCC wrapper) must have grants in System Settings
#   - Must run from LaunchAgent context or local terminal (not SSH — TCC blocks headless)

set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

CAPTURE_DIR="$HOME/.openclaw/findmy-locate"
mkdir -p "$CAPTURE_DIR"

NAME="${1:-}"
if [ -z "$NAME" ]; then
    echo '{"error": "Usage: findmy-locate <name> (dylan, julia, both, me)"}'
    exit 1
fi

# Normalize name
NAME_LOWER="$(echo "$NAME" | tr '[:upper:]' '[:lower:]')"

# Handle "both" by recursing
if [ "$NAME_LOWER" = "both" ] || [ "$NAME_LOWER" = "all" ]; then
    RESULT_DYLAN=$("$0" dylan 2>&1) || true
    RESULT_JULIA=$("$0" julia 2>&1) || true
    echo "{\"results\": [$RESULT_DYLAN, $RESULT_JULIA]}"
    exit 0
fi

# People sidebar order (0-indexed from top)
# Me (clawdbotbochman): position 0
# Julia Jennings:       position 1
# Dylan Bochman:        position 2
case "$NAME_LOWER" in
    dylan|"dylan bochman"|db)
        TARGET_POS=2
        PERSON="Dylan Bochman"
        ;;
    julia|"julia jennings"|jj)
        TARGET_POS=1
        PERSON="Julia Jennings"
        ;;
    me|clawdbot|clawdbotbochman)
        TARGET_POS=0
        PERSON="Me (clawdbotbochman)"
        ;;
    *)
        echo "{\"error\": \"Unknown person: $NAME. Use dylan, julia, both, or me\"}"
        exit 1
        ;;
esac

# Activate FindMy
open -a "Find My"
sleep 1.5

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

# Wait for map to animate and zoom to person's location
sleep 3

# Capture the zoomed view
TIMESTAMP=$(date +%s)
CAPTURE_PATH="$CAPTURE_DIR/findmy-${NAME_LOWER}-${TIMESTAMP}.png"
peekaboo image --app "Find My" --path "$CAPTURE_PATH" >/dev/null 2>&1

# Peekaboo v3 uses "image" not "see" — fall back to "see" if image failed
if [ ! -f "$CAPTURE_PATH" ] || [ "$(stat -f%z "$CAPTURE_PATH" 2>/dev/null || echo 0)" -lt 50000 ]; then
    peekaboo see --app "Find My" --path "$CAPTURE_PATH" >/dev/null 2>&1
fi

# Check for Desktop fallback (peekaboo sometimes saves screenshots there)
if [ ! -f "$CAPTURE_PATH" ] || [ "$(stat -f%z "$CAPTURE_PATH" 2>/dev/null || echo 0)" -lt 50000 ]; then
    DESKTOP_FILE=$(ls -t "$HOME/Desktop/peekaboo_"*.png 2>/dev/null | head -1)
    if [ -n "${DESKTOP_FILE:-}" ] && [ "$(stat -f%z "$DESKTOP_FILE")" -gt 50000 ]; then
        mv "$DESKTOP_FILE" "$CAPTURE_PATH"
    fi
fi

if [ -f "$CAPTURE_PATH" ] && [ "$(stat -f%z "$CAPTURE_PATH" 2>/dev/null || echo 0)" -gt 50000 ]; then
    echo "{\"success\": true, \"person\": \"$PERSON\", \"capture\": \"$CAPTURE_PATH\", \"size\": $(stat -f%z "$CAPTURE_PATH"), \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
else
    echo "{\"error\": \"Failed to capture FindMy for $PERSON — check Peekaboo TCC permissions\", \"person\": \"$PERSON\"}"
    exit 1
fi
