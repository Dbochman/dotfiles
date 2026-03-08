#!/bin/bash
# Wrapper for sync-imessage-groups.py that runs in GUI context
# (needed for Full Disk Access to read chat.db)
/opt/homebrew/bin/python3 /Users/dbochman/.openclaw/bin/sync-imessage-groups.py

# Close this Terminal window after completion to prevent stale window buildup
osascript -e "tell application \"Terminal\" to close (every window whose name contains \"sync-imessage-groups\")" &>/dev/null &
