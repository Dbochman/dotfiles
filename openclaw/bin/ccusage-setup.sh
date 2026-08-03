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
PUSH_SCRIPT="${CCUSAGE_PUSH_SCRIPT:-$DOTFILES_ROOT/openclaw/bin/ccusage-push.sh}"

if [[ ! -x "$PUSH_SCRIPT" ]]; then
  echo "Error: $PUSH_SCRIPT not found or not executable" >&2
  exit 1
fi

LABEL="ai.openclaw.ccusage-push"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.openclaw/logs"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

# Unload existing if running
if launchctl print "$DOMAIN/$LABEL" &>/dev/null; then
  launchctl bootout "$DOMAIN/$LABEL"
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
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/ccusage-push.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/ccusage-push.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>SSH_AUTH_SOCK</key>
        <string></string>
    </dict>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl enable "$DOMAIN/$LABEL"
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed and started $LABEL"
echo "  Plist: $PLIST"
echo "  Script: $PUSH_SCRIPT"
echo "  Interval: every 30 minutes"
