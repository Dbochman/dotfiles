#!/bin/bash
# 8sleep-snapshot.sh — Pre-capture last night's sleep data for both sides.
# Runs before morning briefings (e.g., 6:50 AM) and writes plain-text summaries
# to /tmp/8sleep-{dylan,julia}-latest.txt. Briefing agents just read these files.
# If the Eight Sleep API fails or returns no data, the file is removed so agents
# know to skip the section.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EIGHT_SLEEP="${SCRIPT_DIR}/8sleep"

snapshot_side() {
  local side="$1"
  local outfile="/tmp/8sleep-${side}-latest.txt"

  local output
  if ! output=$("$EIGHT_SLEEP" sleep "$side" 2>&1); then
    rm -f "$outfile"
    return 0
  fi

  # Check for "No sleep data available"
  if echo "$output" | grep -qi "no sleep data"; then
    rm -f "$outfile"
    return 0
  fi

  # Write the snapshot with a timestamp header
  {
    echo "# Eight Sleep — Last Night ($(date '+%Y-%m-%d %H:%M'))"
    echo "$output"
  } > "$outfile"
}

snapshot_side dylan
snapshot_side julia
