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
# FindMy People sidebar (0-indexed from top):
#   0: Me (clawdbotbochman)
#   1: Dylan Bochman
#   2: Julia Jennings
#
# Navigation model:
#   _open_and_reset positions the cursor at 0 (Me) by pressing Up x3.
#   _navigate_and_capture takes a RELATIVE step count from the current
#   cursor position — it does NOT reset. For sequential captures ("both"),
#   each call moves incrementally. For single lookups, the step count is
#   the absolute position from the top.
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
# Helper: press Down N times from CURRENT cursor position, wait for the map
# to settle, then capture the frontmost window.
#
# Args: $1=steps  Number of Down presses from wherever the cursor is now.
#       $2=person Human-readable name for JSON output.
#       $3=tag    Short slug for the filename (e.g. "dylan").
#
# Navigation model:
#   _open_and_reset always leaves the cursor at position 0 (Me).
#   After that, each call to _navigate_and_capture moves the cursor
#   RELATIVE to its current position — it does NOT reset to the top.
#   So for sequential captures (e.g. "both"), call this once with
#   steps=1 for Julia, then again with steps=1 for Dylan.
#   For a single lookup, pass the absolute position (e.g. steps=2
#   for Dylan from the top).
# ---------------------------------------------------------------------------
_navigate_and_capture() {
    local steps="$1" person="$2" tag="$3"

    for i in $(seq 1 "$steps"); do
        peekaboo press down --app "FindMy" >/dev/null 2>&1
        sleep 0.5
    done

    # Wait for map to animate and zoom to the selected person
    sleep 3

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
    # Single pass: open FindMy, navigate to Dylan (pos 1), capture,
    # then down one more to Julia (pos 2), capture.
    _open_and_reset

    # Dylan is at position 1 — navigate down 1 from top
    RESULT_DYLAN=$(_navigate_and_capture 1 "Dylan Bochman" "dylan")

    # Julia is one step below Dylan — navigate down 1 more
    RESULT_JULIA=$(_navigate_and_capture 1 "Julia Jennings" "julia")

    echo "{\"results\": [$RESULT_DYLAN, $RESULT_JULIA]}"
    exit 0
fi

# Single person lookup
case "$NAME_LOWER" in
    dylan|"dylan bochman"|db)
        TARGET_POS=1
        PERSON="Dylan Bochman"
        TAG="dylan"
        ;;
    julia|"julia jennings"|jj)
        TARGET_POS=2
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
