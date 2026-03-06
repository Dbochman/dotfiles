---
name: openclaw-upgrade-plist-overwrite
description: |
  Fix OpenClaw gateway crash-looping after npm upgrade overwrites LaunchAgent plist.
  Use when: (1) Gateway exits with code 1 after `npm install -g openclaw`,
  (2) gateway.err.log shows MissingEnvVarError for BLUEBUBBLES_PASSWORD or other
  secrets, (3) LaunchAgent plist ProgramArguments changed from wrapper script to
  direct node execution, (4) `openclaw install --service` ran as post-install hook
  and replaced the plist. Covers diagnosis, fix, and safeguard for future upgrades.
author: Claude Code
version: 1.0.0
date: 2026-03-06
---

# OpenClaw Upgrade Plist Overwrite

## Problem
`npm install -g openclaw` runs `openclaw install --service` as a post-install hook,
which overwrites `~/Library/LaunchAgents/ai.openclaw.gateway.plist`. The new plist
uses direct node execution with inline `EnvironmentVariables` but is missing secrets
like `BLUEBUBBLES_PASSWORD` that the wrapper script sources from `~/.openclaw/.secrets-cache`.
This causes the gateway to crash-loop with exit code 1.

## Context / Trigger Conditions

- Gateway crash-looping after an OpenClaw npm upgrade
- `launchctl list | grep openclaw` shows non-zero exit code
- `~/.openclaw/logs/gateway.err.log` contains `MissingEnvVarError` or config parse errors
- Plist `ProgramArguments` points to `/opt/homebrew/opt/node@22/bin/node` directly instead of the wrapper app
- BlueBubbles webhooks stop working (gateway not running)

## Root Cause

The `openclaw install --service` post-install hook:
1. Generates a fresh LaunchAgent plist
2. Uses direct node execution: `/opt/homebrew/opt/node@22/bin/node /opt/homebrew/lib/node_modules/openclaw/dist/entry.js gateway`
3. Injects env vars it knows about into `EnvironmentVariables` dict
4. Does NOT know about secrets from `~/.openclaw/.secrets-cache` (e.g., `BLUEBUBBLES_PASSWORD`, `OP_SERVICE_ACCOUNT_TOKEN`)
5. Overwrites the existing plist that used the wrapper script pattern

The wrapper script at `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`
sources all secrets from `.secrets-cache` before exec'ing node, which is the correct pattern
for headless launchd services (no biometric/1Password available).

## Diagnosis

### Step 1: Check gateway status
```bash
ssh dylans-mac-mini 'launchctl list | grep openclaw.gateway'
```
Non-zero exit code (e.g., `1`) confirms crash.

### Step 2: Check error log
```bash
ssh dylans-mac-mini 'tail -50 ~/.openclaw/logs/gateway.err.log'
```
Look for `MissingEnvVarError`, `Config invalid`, or env var substitution failures like
`${BLUEBUBBLES_PASSWORD}` appearing literally in config.

### Step 3: Check if plist was overwritten
```bash
ssh dylans-mac-mini 'plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist'
```
If `ProgramArguments` contains `/opt/homebrew/opt/node@22/bin/node` instead of the
wrapper path (`~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`),
the plist was overwritten.

## Solution

### Step 1: Update wrapper script entry point (if needed)
New OpenClaw versions may change the entry point (e.g., `dist/index.js` to `dist/entry.js`).
Check what the new plist points to and update the wrapper:
```bash
# Check new entry point from the overwritten plist
ssh dylans-mac-mini 'plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist | grep entry'

# Update wrapper script's exec line
ssh dylans-mac-mini 'cat ~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway'
# Edit the exec line to use the correct dist/*.js path
```

### Step 2: Restore the plist
Replace the overwritten plist with the wrapper-based version:
```bash
ssh dylans-mac-mini "cat > ~/Library/LaunchAgents/ai.openclaw.gateway.plist << 'PLIST'
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>ai.openclaw.gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/dbochman/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/dbochman/.openclaw/logs/gateway.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/dbochman/.openclaw/logs/gateway.err.log</string>
</dict>
</plist>
PLIST"
```

### Step 3: Reload the service
```bash
ssh dylans-mac-mini 'launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist'
```

### Step 4: Verify
```bash
ssh dylans-mac-mini 'sleep 3 && launchctl list | grep openclaw.gateway'
```
Exit code should be `0`. Check logs:
```bash
ssh dylans-mac-mini 'tail -5 ~/.openclaw/logs/gateway.log'
```

## Safeguard: Weekly Upgrade Script

The `~/bin/openclaw-weekly-upgrade` script on Mini should backup/restore the plist
around `npm install`:

```bash
# Before npm install
PLIST="$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
PLIST_BAK="/tmp/openclaw-gateway-plist-backup.plist"
cp "$PLIST" "$PLIST_BAK"

# npm install happens here
/opt/homebrew/opt/node@22/bin/npm install -g openclaw@latest

# After npm install — restore plist
if ! diff -q "$PLIST_BAK" "$PLIST" > /dev/null 2>&1; then
    cp "$PLIST_BAK" "$PLIST"
    echo "WARN: plist was overwritten by npm install, restored from backup"
fi
```

## Verification

1. `launchctl list | grep openclaw.gateway` shows exit code `0`
2. `tail ~/.openclaw/logs/gateway.err.log` has no MissingEnvVarError
3. BlueBubbles webhooks resume (send a test iMessage)
4. `plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist` shows wrapper path in ProgramArguments

## Notes

- The wrapper script sources `~/.openclaw/.secrets-cache` which contains `BLUEBUBBLES_PASSWORD`,
  `OP_SERVICE_ACCOUNT_TOKEN`, and other secrets in `KEY=VALUE` format (chmod 600)
- Entry point changed from `dist/index.js` to `dist/entry.js` in v2026.3.2 — check
  the installed version's actual entry point if wrapper fails after upgrade
- The `openclaw install --service` hook cannot be disabled via npm flags
- `launchctl bootout` + `bootstrap` is required (not just `kickstart`) to pick up plist changes
- Gateway hot-reloads `openclaw.json` config changes but plist changes require full service reload
