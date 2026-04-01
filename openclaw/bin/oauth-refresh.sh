#!/bin/bash
# oauth-refresh.sh — Self-contained OAuth token refresh for the Mac Mini.
# Runs `claude auth login` using CLAUDE_CODE_OAUTH_REFRESH_TOKEN to get a
# fresh access token without browser or keychain access, then copies the
# credentials to ~/.openclaw/.anthropic-oauth-cache for the ring-listener
# and other services that need Anthropic API access.
#
# The refresh token rotates on each login — the new one is stored in
# ~/.claude/.credentials.json and used for the next refresh cycle.

set -euo pipefail

CREDS="$HOME/.claude/.credentials.json"
CACHE="$HOME/.openclaw/.anthropic-oauth-cache"
CLAUDE="/opt/homebrew/bin/claude"

if [[ ! -x "$CLAUDE" ]]; then
  echo "ERROR: claude not found at $CLAUDE" >&2
  exit 1
fi

# Read refresh token and scopes from the last credentials file
if [[ ! -f "$CREDS" ]]; then
  echo "ERROR: $CREDS not found — run 'claude auth login' manually first" >&2
  exit 1
fi

REFRESH_TOKEN=$(python3 -c "import json; print(json.load(open('$CREDS')).get('claudeAiOauth',{}).get('refreshToken',''))")
SCOPES=$(python3 -c "import json; s=json.load(open('$CREDS')).get('claudeAiOauth',{}).get('scopes',[]); print(' '.join(s) if isinstance(s,list) else s)")

if [[ -z "$REFRESH_TOKEN" ]]; then
  echo "ERROR: no refreshToken in $CREDS" >&2
  exit 1
fi

# Refresh the token (writes new access + refresh token to ~/.claude/.credentials.json)
CLAUDE_CODE_OAUTH_REFRESH_TOKEN="$REFRESH_TOKEN" \
CLAUDE_CODE_OAUTH_SCOPES="$SCOPES" \
  "$CLAUDE" auth login 2>&1

# Copy to the location the ring-listener and other services read from
cp "$CREDS" "$CACHE"
chmod 600 "$CACHE"

echo "OK: token refreshed at $(date -u +%FT%TZ)" >&2
