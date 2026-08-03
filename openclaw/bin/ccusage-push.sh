#!/bin/bash
# ccusage-push.sh — Collect Codex CLI token usage for the Mac Mini dashboard.
# Runs via LaunchAgent every 30 minutes on any machine with Codex activity.
# Outputs daily JSON from ccusage, then installs it locally or pushes it to Mini.
#
# Environment variables (set in LaunchAgent or shell):
#   CCUSAGE_MINI_HOST  — ssh target for Mini (default: dbochman@dylans-mac-mini)
#   CCUSAGE_REMOTE_DIR — remote path for JSON file (default: ~/.openclaw/usage-history)
#   CCUSAGE_LOCAL_DIR  — local destination directory; skips SSH when set
#   CCUSAGE_SSH_IDENTITY — unattended SSH key (auto-detects id_launchd, then id_rsa)
#   CCUSAGE_PACKAGE — pinned ccusage package spec (default: ccusage@20.0.19)

set -euo pipefail

# Clear SSH_AUTH_SOCK so ccusage's internal git operations don't trigger
# 1Password agent prompts. Mini SSH auth is handled by ~/.ssh/id_launchd
# (configured in ~/.ssh/config with IdentityAgent none).
export SSH_AUTH_SOCK=""

MINI="${CCUSAGE_MINI_HOST:-dbochman@dylans-mac-mini}"
REMOTE_DIR="${CCUSAGE_REMOTE_DIR:-~/.openclaw/usage-history}"
LOCAL_DIR="${CCUSAGE_LOCAL_DIR:-}"
CCUSAGE_PACKAGE="${CCUSAGE_PACKAGE:-ccusage@20.0.19}"
# Per-machine filename using hostname (lowercase, no domain)
MACHINE=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo "unknown")
LOCAL_TMP=$(mktemp "${TMPDIR:-/tmp}/ccusage-daily-${MACHINE}.XXXXXX")
LOCAL_INSTALL=""
cleanup() {
  rm -f "$LOCAL_TMP"
  if [[ -n "$LOCAL_INSTALL" ]]; then
    rm -f "$LOCAL_INSTALL"
  fi
}
trap cleanup EXIT
DESTINATION_NAME="ccusage-codex-${MACHINE}.json"

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

# Get last 90 days of daily usage
SINCE=$(date -v-90d +%Y-%m-%d 2>/dev/null || date -d '90 days ago' +%Y-%m-%d)

# Run the explicit Codex reader. Unified mode is intentionally avoided because
# OpenClaw and retained Claude history are accounted for elsewhere.
if ! "$NPX" --yes "$CCUSAGE_PACKAGE" codex daily \
  --json --breakdown --offline --since "$SINCE" > "$LOCAL_TMP" 2>/dev/null; then
  echo "ccusage failed" >&2
  exit 1
fi

# Validate JSON
if ! /usr/bin/python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert isinstance(data,dict) and isinstance(data.get("daily"),list)' "$LOCAL_TMP" 2>/dev/null; then
  echo "Invalid JSON output" >&2
  exit 1
fi

# The Mini writes its own observation atomically. Other machines transfer their
# per-host file with an explicit unattended SSH identity.
if [[ -n "$LOCAL_DIR" ]]; then
  if [[ -L "$LOCAL_DIR" ]]; then
    echo "local usage directory must not be a symlink" >&2
    exit 1
  fi
  mkdir -p "$LOCAL_DIR"
  LOCAL_DESTINATION="$LOCAL_DIR/$DESTINATION_NAME"
  LOCAL_INSTALL=$(mktemp "$LOCAL_DIR/.${DESTINATION_NAME}.XXXXXX")
  cp "$LOCAL_TMP" "$LOCAL_INSTALL"
  chmod 600 "$LOCAL_INSTALL"
  mv -f "$LOCAL_INSTALL" "$LOCAL_DESTINATION"
  LOCAL_INSTALL=""
else
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

  "$SCP" -q -i "$SSH_IDENTITY" -o IdentityAgent=none -o IdentitiesOnly=yes \
    "$LOCAL_TMP" "$MINI:$REMOTE_DIR/$DESTINATION_NAME" 2>/dev/null || {
    echo "scp to Mini failed" >&2
    exit 1
  }
fi

echo "Codex usage data refreshed at $(date -u +%FT%TZ)" >&2
