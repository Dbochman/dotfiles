#!/bin/bash
# Wrapper for home-state-snapshot.py — sources secrets before running.
# Used by ai.openclaw.home-state-snapshot LaunchAgent.

set -euo pipefail

export PATH="/Users/dbochman/.openclaw/bin:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/dbochman"

# Source secrets for API auth
set -a
source "$HOME/.openclaw/.secrets-cache"
set +a

exec /usr/bin/python3 "$HOME/.openclaw/bin/home-state-snapshot.py"
