#!/bin/bash
# ring-listener-wrapper.sh — Start Ring event listener with secrets loaded
# Runs as a persistent LaunchAgent (ai.openclaw.ring-listener)

set -euo pipefail

# Load secrets (cache-only pattern — no op read, avoids launchd hang)
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a
  source "$HOME/.openclaw/.secrets-cache"
  set +a
fi

PYTHON="$HOME/.openclaw/ring/venv/bin/python3"
LISTENER="$HOME/.openclaw/skills/ring-doorbell/ring-listener.py"

exec "$PYTHON" "$LISTENER"
