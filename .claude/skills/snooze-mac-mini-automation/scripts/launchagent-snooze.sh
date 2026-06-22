#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: launchagent-snooze.sh <pause|status|resume>

Temporarily unload browser-based Mac Mini LaunchAgents and restore only the
jobs that were loaded before the snooze.
EOF
}

action="${1:-}"
case "$action" in
  pause|status|resume) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

host="${MAC_MINI_HOST:-mac-mini}"

ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  /Users/dbochman/.openclaw/bin/launchagent-snooze "$action"
