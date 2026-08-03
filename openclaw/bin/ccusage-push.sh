#!/bin/bash
# ccusage-push.sh — Collect Claude Code token usage and push to Mac Mini.
# Runs via LaunchAgent every 30 minutes on any machine with Claude Code.
# Outputs daily JSON from ccusage, then scp's to Mini for dashboard consumption.
#
# Environment variables (set in LaunchAgent or shell):
#   CCUSAGE_MINI_HOST  — ssh target for Mini (default: dbochman@dylans-mac-mini)
#   CCUSAGE_REMOTE_DIR — remote path for JSON file (default: ~/.openclaw/usage-history)
#   CCUSAGE_SSH_IDENTITY — unattended SSH key (auto-detects id_launchd, then id_rsa)

set -euo pipefail

# Clear SSH_AUTH_SOCK so ccusage's internal git operations don't trigger
# 1Password agent prompts. Mini SSH auth is handled by ~/.ssh/id_launchd
# (configured in ~/.ssh/config with IdentityAgent none).
export SSH_AUTH_SOCK=""

MINI="${CCUSAGE_MINI_HOST:-dbochman@dylans-mac-mini}"
REMOTE_DIR="${CCUSAGE_REMOTE_DIR:-~/.openclaw/usage-history}"
# Per-machine filename using hostname (lowercase, no domain)
MACHINE=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo "unknown")
LOCAL_TMP=$(mktemp "${TMPDIR:-/tmp}/ccusage-daily-${MACHINE}.XXXXXX")
trap 'rm -f "$LOCAL_TMP"' EXIT

# Find npx — prefer the managed Node 22 keg used by the rest of the tooling.
NPX="${CCUSAGE_NPX:-}"
if [[ -z "$NPX" ]]; then
  for p in /opt/homebrew/opt/node@22/bin/npx /opt/homebrew/bin/npx /usr/local/bin/npx; do
    [[ -x "$p" ]] && NPX="$p" && break
  done
fi
if [[ -z "$NPX" ]] || [[ ! -x "$NPX" ]]; then
  echo "npx not found" >&2
  exit 1
fi

SCP="${CCUSAGE_SCP:-/usr/bin/scp}"
if [[ ! -x "$SCP" ]]; then
  echo "scp not found" >&2
  exit 1
fi

SSH_IDENTITY="${CCUSAGE_SSH_IDENTITY:-}"
if [[ -z "$SSH_IDENTITY" ]]; then
  for identity in "$HOME/.ssh/id_launchd" "$HOME/.ssh/id_rsa"; do
    if [[ -f "$identity" ]] && [[ ! -L "$identity" ]]; then
      SSH_IDENTITY="$identity"
      break
    fi
  done
fi
if [[ -z "$SSH_IDENTITY" ]] || [[ ! -f "$SSH_IDENTITY" ]] || [[ -L "$SSH_IDENTITY" ]]; then
  echo "unattended SSH identity not found" >&2
  exit 1
fi

# Get last 90 days of daily usage
SINCE=$(date -v-90d +%Y%m%d 2>/dev/null || date -d '90 days ago' +%Y%m%d)

# Run ccusage with JSON output (--offline to avoid network dependency)
if ! "$NPX" ccusage daily --json --breakdown --offline --since "$SINCE" > "$LOCAL_TMP" 2>/dev/null; then
  echo "ccusage failed" >&2
  exit 1
fi

# Validate JSON
if ! /usr/bin/python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert isinstance(data,dict) and isinstance(data.get("daily"),list)' "$LOCAL_TMP" 2>/dev/null; then
  echo "Invalid JSON output" >&2
  exit 1
fi

# Push to Mini (auth via ~/.ssh/id_launchd, configured in ssh config)
"$SCP" -q -i "$SSH_IDENTITY" -o IdentityAgent=none -o IdentitiesOnly=yes \
  "$LOCAL_TMP" "$MINI:$REMOTE_DIR/ccusage-${MACHINE}.json" 2>/dev/null || {
  echo "scp to Mini failed" >&2
  exit 1
}

echo "ccusage data pushed at $(date -u +%FT%TZ)" >&2
