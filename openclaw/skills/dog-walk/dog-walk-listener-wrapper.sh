#!/bin/bash
# dog-walk-listener-wrapper.sh — Start dog walk listener with secrets loaded
# Runs as a persistent LaunchAgent (ai.openclaw.dog-walk-listener)

set -euo pipefail

# Load secrets (cache-only pattern — no op read, avoids launchd hang)
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a
  source "$HOME/.openclaw/.secrets-cache"
  set +a
fi

PYTHON="$HOME/.openclaw/ring/venv/bin/python3"
LISTENER="$HOME/.openclaw/skills/dog-walk/dog-walk-listener.py"

exec "$PYTHON" "$LISTENER"
