---
name: macos-applescript-tcc-hang
description: |
  Fix AppleScript hanging indefinitely when automating macOS apps (Messages, Mail, etc.)
  due to stale TCC (Transparency, Consent, and Control) Automation entries. Use when:
  (1) `osascript` hangs on `tell application "Messages"` operations like sending, counting
  chats, or accessing accounts, (2) Simple AppleScript like `tell app "Messages" to name`
  works but any operation touching accounts/chats/participants hangs forever, (3) AppleScript
  returns "AppleEvent timed out (-1712)" after ~2 minutes, (4) Automation that previously
  worked stops working after macOS update or system changes, (5) Rebooting doesn't fix the
  hang. The fix is `tccutil reset AppleEvents` followed by re-approving permission popups.
author: Claude Code
version: 1.0.0
date: 2026-02-08
---

# macOS AppleScript TCC Automation Hang

## Problem

AppleScript automation of macOS apps (especially Messages.app) hangs indefinitely or
times out with error `-1712`. The TCC (Transparency, Consent, and Control) database
entries for AppleEvents become stale or corrupted, causing the AppleScript scripting
bridge to block on operations that access app internals (accounts, chats, contacts)
while simple property queries (`name`) still work.

## Context / Trigger Conditions

- `osascript -e 'tell application "Messages" to name'` returns immediately (works)
- `osascript -e 'tell application "Messages" to count of chats'` hangs forever
- `osascript -e 'tell application "Messages" to send "hi" to buddy "+1..." of (1st account whose service type = iMessage)'` hangs forever
- After ~120 seconds, may return: `execution error: Messages got an error: AppleEvent timed out. (-1712)`
- Rebooting the machine does NOT fix the issue
- Killing and restarting Messages.app does NOT fix it
- The automation previously worked (e.g., OpenClaw/imsg sending messages via AppleScript)
- macOS Tahoe (26.x) — may affect other versions too

## Root Cause

macOS stores AppleEvents (Automation) permissions in the TCC database at
`~/Library/Application Support/com.apple.TCC/TCC.db`. When entries become stale
(after macOS updates, app updates, or process identity changes), the scripting bridge
gets stuck trying to validate permissions rather than prompting for new consent.

The key insight is that the TCC subsystem doesn't cleanly fail — it hangs the
AppleEvent dispatch, making the calling process block indefinitely.

## Solution

### Step 1: Reset AppleEvents TCC entries

```bash
tccutil reset AppleEvents
```

This clears ALL Automation permission entries. Every app that uses AppleScript to
control other apps will need to re-authorize.

### Step 2: Trigger re-authorization

Run an AppleScript that triggers the permission prompt:

```bash
osascript -e 'tell application "Messages" to send "test" to buddy "+1XXXXXXXXXX" of (1st account whose service type = iMessage)'
```

**Important:** This must run from a context that has GUI access:
- Directly in Terminal.app
- Via `open /path/to/script.command` (runs in GUI session)
- NOT from SSH (will hang because the permission dialog can't render)

### Step 3: Approve the macOS permission popup

A system dialog will appear: *"Terminal.app wants to control Messages.app"*
(or whatever app is calling osascript). Click **OK/Allow**.

### Step 4: Restart services that use AppleScript

Any background services (like OpenClaw gateway) that use `osascript` need to be
restarted so their child processes get fresh TCC authorization:

```bash
launchctl stop ai.openclaw.gateway
launchctl start ai.openclaw.gateway
```

The first send attempt from the service may trigger another permission popup
for the service's app bundle identity.

## Verification

After resetting and approving:

```bash
# Should return immediately with the message sent
osascript -e 'tell application "Messages" to send "test" to buddy "+1XXXXXXXXXX" of (1st account whose service type = iMessage)'

# Should return a number (not hang)
osascript -e 'tell application "Messages" to count of chats'
```

## Diagnostic Steps

Before resetting TCC, confirm the issue is TCC-related:

```bash
# Test 1: Simple property (should work even with stale TCC)
osascript -e 'tell application "Messages" to name'
# Expected: "Messages" (instant)

# Test 2: Internal object access (hangs with stale TCC)
osascript -e 'tell application "Messages" to count of accounts'
# Expected if broken: hangs, then "AppleEvent timed out (-1712)"

# Test 3: Send attempt
osascript -e 'tell application "Messages" to send "test" to buddy "+1..." of (1st account whose service type = iMessage)'
# Expected if broken: hangs indefinitely
```

If Test 1 works but Test 2/3 hangs, the issue is stale TCC entries.

## Notes

- `tccutil reset AppleEvents` is a nuclear option — it resets permissions for ALL apps.
  You can try resetting for a specific app first: `tccutil reset AppleEvents com.apple.Terminal`
  but this doesn't always work for the scripting bridge.
- After reset, you may need to approve multiple permission popups (one per calling app).
- For headless services (LaunchAgents), the `osascript` call must originate from a process
  that macOS associates with a GUI-capable app bundle. Using `open -jg script.command`
  from a LaunchAgent runs the script in the Aqua session where permission dialogs can appear.
- This issue is distinct from the `op read` TCC hang (which is Mach port / Security framework
  based) — see skill `1password-cli-launchd-hang`.
- On macOS Tahoe (26.x), this appears to happen more frequently, possibly due to changes
  in the TCC subsystem.
- The `chat id` AppleScript path (for group chats) is affected the same way as the `buddy`
  path (for DMs) — both hang when TCC is stale.
