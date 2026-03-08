#!/bin/bash
# ccusage-setup.sh — Install the ccusage-push LaunchAgent on this machine.
# Generates the plist with correct paths for the current user/machine.
# Safe to re-run — unloads existing agent first.
#
# Usage: bash openclaw/bin/ccusage-setup.sh [dotfiles-root]
#   dotfiles-root defaults to the parent of this script's directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOTFILES_ROOT="${1:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PUSH_SCRIPT="$DOTFILES_ROOT/openclaw/bin/ccusage-push.sh"

if [[ ! -x "$PUSH_SCRIPT" ]]; then
  echo "Error: $PUSH_SCRIPT not found or not executable" >&2
  exit 1
fi

LABEL="ai.openclaw.ccusage-push"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# Unload existing if running
if launchctl list "$LABEL" &>/dev/null; then
  launchctl unload "$PLIST" 2>/dev/null || true
  echo "Unloaded existing $LABEL"
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PUSH_SCRIPT</string>
    </array>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>StandardOutPath</key>
    <string>/tmp/ccusage-push.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ccusage-push.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

launchctl load -w "$PLIST"
echo "Installed and started $LABEL"
echo "  Plist: $PLIST"
echo "  Script: $PUSH_SCRIPT"
echo "  Interval: every 30 minutes"
