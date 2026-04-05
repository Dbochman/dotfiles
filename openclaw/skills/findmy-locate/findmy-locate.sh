#!/bin/bash
# findmy-locate — Navigate to a person in FindMy and capture their zoomed map location
#
# Usage: findmy-locate <name>
#   name: dylan, julia, both, or me
#
# Uses Peekaboo keyboard navigation to select people in the FindMy sidebar,
# then captures the frontmost window screenshot.
#
# NOTE: Peekaboo uses different app name resolution for press vs image:
#   - press --app "FindMy"  (process name, no space)
#   - image --mode frontmost (most reliable for screenshot capture)
#
# People order in sidebar: Me (0) → Julia Jennings (1) → Dylan Bochman (2)
#
# Requires:
#   - peekaboo with Screen Recording + Accessibility TCC grants
#   - Must run from local terminal or LaunchAgent GUI context (not SSH)

set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

CAPTURE_DIR="$HOME/.openclaw/findmy-locate"
mkdir -p "$CAPTURE_DIR"

NAME="${1:-}"
if [ -z "$NAME" ]; then
    echo '{"error": "Usage: findmy-locate <name> (dylan, julia, both, me)"}'
    exit 1
fi

NAME_LOWER="$(echo "$NAME" | tr '[:upper:]' '[:lower:]')"

# ---------------------------------------------------------------------------
# Helper: navigate to sidebar position and capture
# Args: $1=position (0-indexed), $2=person label, $3=file tag
# Assumes FindMy is already frontmost and cursor is at position 0 (Me).
# ---------------------------------------------------------------------------
_navigate_and_capture() {
    local target_pos="$1" person="$2" tag="$3"

    # Navigate down from current position (0) to target
    for i in $(seq 1 "$target_pos"); do
        peekaboo press down --app "FindMy" >/dev/null 2>&1
        sleep 0.5
    done

    # Wait for map to animate and zoom
    sleep 3

    # Capture
    local ts
    ts=$(date +%s)
    local capture_path="$CAPTURE_DIR/findmy-${tag}-${ts}.png"
    peekaboo image --mode frontmost --path "$capture_path" >/dev/null 2>&1

    if [ -f "$capture_path" ] && [ "$(stat -f%z "$capture_path" 2>/dev/null || echo 0)" -gt 50000 ]; then
        echo "{\"success\": true, \"person\": \"$person\", \"capture\": \"$capture_path\", \"size\": $(stat -f%z "$capture_path"), \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
    else
        echo "{\"error\": \"Failed to capture FindMy for $person\", \"person\": \"$person\"}"
    fi
}

# ---------------------------------------------------------------------------
# Open FindMy and reset to top of People sidebar
# ---------------------------------------------------------------------------
_open_and_reset() {
    open -a "FindMy" || open -a "Find My"
    sleep 1.5

    # Navigate to top of list (press Up enough times to reach "Me" at position 0)
    for i in 1 2 3; do
        peekaboo press up --app "FindMy" >/dev/null 2>&1
        sleep 0.3
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [ "$NAME_LOWER" = "both" ] || [ "$NAME_LOWER" = "all" ]; then
    # Single pass: open FindMy, navigate to Julia (pos 1), capture,
    # then down one more to Dylan (pos 2), capture.
    _open_and_reset

    # Julia is at position 1 — navigate down 1 from top
    RESULT_JULIA=$(_navigate_and_capture 1 "Julia Jennings" "julia")

    # Dylan is one step below Julia — navigate down 1 more
    RESULT_DYLAN=$(_navigate_and_capture 1 "Dylan Bochman" "dylan")

    echo "{\"results\": [$RESULT_JULIA, $RESULT_DYLAN]}"
    exit 0
fi

# Single person lookup
case "$NAME_LOWER" in
    dylan|"dylan bochman"|db)
        TARGET_POS=2
        PERSON="Dylan Bochman"
        TAG="dylan"
        ;;
    julia|"julia jennings"|jj)
        TARGET_POS=1
        PERSON="Julia Jennings"
        TAG="julia"
        ;;
    me|clawdbot|clawdbotbochman)
        TARGET_POS=0
        PERSON="Me (clawdbotbochman)"
        TAG="me"
        ;;
    *)
        echo "{\"error\": \"Unknown person: $NAME. Use dylan, julia, both, or me\"}"
        exit 1
        ;;
esac

_open_and_reset
RESULT=$(_navigate_and_capture "$TARGET_POS" "$PERSON" "$TAG")
echo "$RESULT"

# Exit non-zero if capture failed
echo "$RESULT" | grep -q '"success"' || exit 1
