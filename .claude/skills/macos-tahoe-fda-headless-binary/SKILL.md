---
name: macos-tahoe-fda-headless-binary
description: |
  Grant Full Disk Access (FDA) to headless CLI binaries on macOS Tahoe (26.x) when they
  need to access TCC-protected paths like ~/Library/Messages/chat.db. Use when:
  (1) A Mach-O binary hangs or returns "authorization denied (code: 23)" accessing
  ~/Library/Messages, ~/Library/Mail, or similar TCC-protected directories,
  (2) `tccutil reset SystemPolicyAllFiles` was run and now nothing can access protected
  paths, (3) The System Settings FDA picker won't show a bare CLI binary after clicking "+",
  (4) A LaunchAgent-spawned process that previously had FDA stops working after a service
  restart or npm/brew upgrade, (5) `ls ~/Library/Messages/` returns "Interrupted system call"
  over SSH. Covers the .app wrapper trick to make bare binaries visible in the FDA picker.
author: Claude Code
version: 1.0.0
date: 2026-02-22
---

# macOS Tahoe: Grant FDA to Headless CLI Binaries

## Problem

On macOS Tahoe (26.x), CLI binaries (Mach-O executables installed via Homebrew or npm)
that need to read TCC-protected paths like `~/Library/Messages/chat.db` require Full Disk
Access (FDA). Unlike GUI apps, these binaries:

1. Don't trigger TCC permission prompts when run from launchd (they just hang or get denied)
2. Don't appear in the System Settings FDA picker when you try to add them via the `+` button
3. Lose their FDA grants when `tccutil reset SystemPolicyAllFiles` is run
4. May lose grants after the binary is updated (brew upgrade, npm install -g)

## Context / Trigger Conditions

- A process accessing `~/Library/Messages/chat.db` (or similar TCC path) hangs indefinitely
- `osascript -e 'do shell script "/path/to/binary ..."'` returns `permissionDenied(path: "...", underlying: authorization denied (code: 23))`
- Over SSH: `ls ~/Library/Messages/` returns "Interrupted system call" then hangs
- Over SSH: `cp ~/Library/Messages/chat.db /tmp/` fails with "Interrupted system call"
- Over SSH: `sqlite3 ~/Library/Messages/chat.db "SELECT 1"` hangs forever
- A LaunchAgent service that was working stops after restart/upgrade
- The `+` button in System Settings > Privacy & Security > Full Disk Access lets you
  browse to the binary but it doesn't appear in the list after adding

## Root Cause

macOS Tahoe's TCC subsystem enforces Full Disk Access at the process level. For
launchd-spawned processes, TCC checks are non-interactive — there's no popup, just a
silent hang (the process blocks on the `open()` syscall waiting for authorization that
never comes) or an immediate denial.

The System Settings FDA picker is designed for `.app` bundles. Bare Mach-O executables
(like Homebrew-installed binaries) are technically addable but often fail to appear in the
list due to missing bundle metadata.

On Tahoe, launchd processes get **transient TCC sessions**. FDA grants may not persist
across service restarts because launchd creates a new security session each time. This
is different from pre-Tahoe behavior where grants were sticky.

## Solution

### Step 1: Create an `.app` Wrapper

Create a minimal macOS app bundle that symlinks to the real binary:

```bash
BINARY_PATH="/opt/homebrew/Cellar/imsg/0.5.0/libexec/imsg"
BUNDLE_ID="com.steipete.imsg"  # Use the binary's actual code-signing identifier
APP_NAME="imsg"

mkdir -p ~/Applications/${APP_NAME}.app/Contents/MacOS
ln -sf "$BINARY_PATH" ~/Applications/${APP_NAME}.app/Contents/MacOS/${APP_NAME}

cat > ~/Applications/${APP_NAME}.app/Contents/Info.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_ID}</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>${APP_NAME}</string>
  <key>CFBundleVersion</key>
  <string>1.0.0</string>
</dict>
</plist>
EOF
```

To find the binary's code-signing identifier:
```bash
codesign -dv /path/to/binary 2>&1 | grep Identifier
```

### Step 2: Add to FDA via System Settings

1. Open System Settings > Privacy & Security > Full Disk Access
   ```bash
   open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
   ```
2. Click `+`
3. Navigate to `~/Applications/` (or `/Users/username/Applications/`)
4. Select the `.app` wrapper (e.g., `imsg.app`)
5. Ensure the toggle is **ON**

### Step 3: Also Grant FDA to Parent Processes

If the binary is spawned by a LaunchAgent, the parent process may also need FDA.
Add these to the FDA list as well:
- The gateway/service app bundle (e.g., `OpenClawGateway.app`)
- `Terminal.app` (for SSH access)
- `Messages.app` (if accessing Messages data)

### Step 4: Restart the Service

```bash
launchctl stop ai.openclaw.gateway
launchctl start ai.openclaw.gateway
```

## Verification

After granting FDA:

```bash
# From SSH - should list files without hanging
ls ~/Library/Messages/

# From osascript (GUI context) - should return data, not "authorization denied"
osascript -e 'do shell script "/opt/homebrew/bin/imsg chats list 2>&1 | head -3"'

# The LaunchAgent process should stay running (no restart loop)
tail -f ~/.openclaw/logs/gateway.log
# Should NOT see: "imsg rpc not ready after Xms" or "auto-restart attempt"
```

## Important Caveats

### Don't Use `tccutil reset SystemPolicyAllFiles` Casually

This command **nukes ALL Full Disk Access grants** for all apps. After running it:
- Every app that had FDA needs to be re-added manually
- Headless processes will hang until re-authorized
- Must be done from the Mac's physical screen or VNC (not SSH)
- There's no way to re-add grants programmatically on Tahoe

### Binary Updates Invalidate Grants

When a binary is updated (e.g., `brew upgrade imsg`), the FDA grant may be invalidated
if the code signature changes. The `.app` wrapper uses a symlink, so if the symlink
target path changes (new version directory), you need to:
1. Update the symlink: `ln -sf /new/path ~/Applications/imsg.app/Contents/MacOS/imsg`
2. Toggle the FDA entry off and on in System Settings, or remove and re-add it

### SSH vs LaunchAgent vs GUI

| Context | TCC Behavior |
|---------|-------------|
| GUI (Terminal.app) | TCC prompt appears, user can click Allow |
| LaunchAgent (launchd) | No prompt — silent hang or denial |
| SSH | No prompt — "Interrupted system call" then hang |
| osascript (do shell script) | No prompt — immediate denial with error code 23 |

### The `.app` Wrapper Trick

The reason this works is that macOS System Settings' FDA picker specifically looks for
app bundles (directories ending in `.app` with a valid `Info.plist`). The `CFBundleIdentifier`
in the plist should match the binary's code-signing identifier when possible, though
macOS primarily uses the executable path for TCC matching.

## Notes

- This issue is specific to macOS Tahoe (26.x). Earlier macOS versions had stickier
  TCC grants for launchd services.
- The `imsg` binary (by @steipete) is a common case — it's a Swift CLI tool that reads
  `~/Library/Messages/chat.db` via SQLite.
- For the OpenClaw gateway, the full FDA chain is: `OpenClawGateway.app` (wrapper) →
  `node` → spawns `imsg rpc` → reads `chat.db`. Both `imsg.app` and `OpenClawGateway.app`
  need FDA.
- On headless Mac Minis, VNC/Screen Sharing is the only way to interact with the
  System Settings FDA UI. Ensure Screen Sharing is enabled before you need it.
