#!/bin/bash
# oauth-refresh.sh — Self-contained OAuth token refresh for the Mac Mini.
# Runs `claude auth login` using CLAUDE_CODE_OAUTH_REFRESH_TOKEN to get a
# fresh access token, then writes the credentials to the oauth-cache file
# for the dog-walk listener and other services.
#
# Claude Code writes credentials to the macOS keychain when `security` is
# available. To force file-based storage (~/.claude/.credentials.json),
# we hide `security` from PATH during login. This ensures the refresh
# token is always readable without GUI/keychain access.

set -euo pipefail

CREDS="$HOME/.claude/.credentials.json"
CACHE="$HOME/.openclaw/.anthropic-oauth-cache"
CLAUDE="/opt/homebrew/bin/claude"

if [[ ! -x "$CLAUDE" ]]; then
  echo "ERROR: claude not found at $CLAUDE" >&2
  exit 1
fi

# Read refresh token + scopes from credentials file or cache
read_creds() {
  local json=""

  if [[ -f "$CREDS" ]]; then
    json=$(cat "$CREDS")
    echo "Read credentials from $CREDS" >&2
  elif [[ -f "$CACHE" ]]; then
    json=$(cat "$CACHE")
    echo "Read credentials from $CACHE" >&2
  else
    echo "ERROR: no credentials found in $CREDS or $CACHE" >&2
    exit 1
  fi

  REFRESH_TOKEN=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('claudeAiOauth',{}).get('refreshToken',''))" <<< "$json")
  SCOPES=$(python3 -c "import json,sys; s=json.loads(sys.stdin.read()).get('claudeAiOauth',{}).get('scopes',[]); print(' '.join(s) if isinstance(s,list) else s)" <<< "$json")
}

read_creds

if [[ -z "$REFRESH_TOKEN" ]]; then
  echo "ERROR: no refreshToken found" >&2
  exit 1
fi

# Build a PATH without /usr/bin so `security` (keychain CLI) is not found.
# This forces claude auth login to use file-based credential storage
# (~/.claude/.credentials.json) instead of the macOS keychain, which is
# unreadable over SSH / in headless contexts.
RESTRICTED_PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/bin"

CLAUDE_CODE_OAUTH_REFRESH_TOKEN="$REFRESH_TOKEN" \
CLAUDE_CODE_OAUTH_SCOPES="$SCOPES" \
PATH="$RESTRICTED_PATH" \
  "$CLAUDE" auth login 2>&1

# Verify the file was written
if [[ ! -f "$CREDS" ]]; then
  echo "ERROR: $CREDS not created after login" >&2
  exit 1
fi

# Copy to oauth-cache for dog-walk listener and other services
cp "$CREDS" "$CACHE"
chmod 600 "$CACHE"

echo "OK: token refreshed at $(date -u +%FT%TZ)" >&2
