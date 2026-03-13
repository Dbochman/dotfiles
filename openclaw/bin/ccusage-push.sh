#!/bin/bash
# ccusage-push.sh — Collect Claude Code token usage and push to Mac Mini.
# Runs via LaunchAgent every 30 minutes on any machine with Claude Code.
# Outputs daily JSON from ccusage, then scp's to Mini for dashboard consumption.
#
# Environment variables (set in LaunchAgent or shell):
#   CCUSAGE_MINI_HOST  — ssh target for Mini (default: dbochman@dylans-mac-mini)
#   CCUSAGE_REMOTE_DIR — remote path for JSON file (default: ~/.openclaw/usage-history)

set -euo pipefail

# Clear SSH_AUTH_SOCK so ccusage's internal git operations don't trigger
# 1Password agent prompts. Mini SSH auth is handled by ~/.ssh/id_launchd
# (configured in ~/.ssh/config with IdentityAgent none).
export SSH_AUTH_SOCK=""

MINI="${CCUSAGE_MINI_HOST:-dbochman@dylans-mac-mini}"
REMOTE_DIR="${CCUSAGE_REMOTE_DIR:-~/.openclaw/usage-history}"
# Per-machine filename using hostname (lowercase, no domain)
MACHINE=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo "unknown")
LOCAL_TMP="/tmp/ccusage-daily-${MACHINE}.json"

# Find npx — check common locations
NPX=""
for p in /usr/local/bin/npx /opt/homebrew/bin/npx; do
  [[ -x "$p" ]] && NPX="$p" && break
done
if [[ -z "$NPX" ]]; then
  NPX=$(command -v npx 2>/dev/null || true)
fi
if [[ -z "$NPX" ]]; then
  echo "npx not found" >&2
  exit 0
fi

# Get last 90 days of daily usage
SINCE=$(date -v-90d +%Y%m%d 2>/dev/null || date -d '90 days ago' +%Y%m%d)

# Run ccusage with JSON output (--offline to avoid network dependency)
if ! "$NPX" ccusage daily --json --breakdown --offline --since "$SINCE" > "$LOCAL_TMP" 2>/dev/null; then
  echo "ccusage failed" >&2
  exit 0  # Don't fail the LaunchAgent
fi

# Validate JSON
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$LOCAL_TMP" 2>/dev/null; then
  echo "Invalid JSON output" >&2
  exit 0
fi

# Push to Mini (auth via ~/.ssh/id_launchd, configured in ssh config)
scp -q "$LOCAL_TMP" "$MINI:$REMOTE_DIR/ccusage-${MACHINE}.json" 2>/dev/null || {
  echo "scp to Mini failed" >&2
  exit 0
}

echo "ccusage data pushed at $(date -u +%FT%TZ)" >&2
