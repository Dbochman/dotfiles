---
name: openclaw-browser-cdp-crash
description: |
  Fix OpenClaw browser service "Failed to start Chrome CDP on port 18800" error.
  Use when: (1) OpenClaw agent can't use browser tool, (2) gateway logs show
  "Failed to start Chrome CDP on port 18800 for profile openclaw", (3) browser
  crashes with Trace/BPT trap or SIGTRAP, (4) Chrome exits within 1-2 seconds
  of starting. Covers stale SingletonLock files, corrupted profiles with
  encrypted Keychain tokens, and orphaned Chrome processes.
author: Claude Code
version: 1.0.0
date: 2026-02-09
---

# OpenClaw Browser CDP Crash

## Problem
OpenClaw's browser service fails to start Chrome with CDP (Chrome DevTools Protocol) on port 18800, preventing the agent from using browser-based skills (Amazon shopping, etc).

## Context / Trigger Conditions
- Gateway log shows: `Failed to start Chrome CDP on port 18800 for profile "openclaw"`
- Gateway stderr shows: `Can't reach the OpenClaw browser control service`
- The failure happens within ~1 second of launch (OpenClaw's 15-second timeout never reached)
- Often cascades into EPIPE crash when iMessage pipe breaks

## Key Architecture Facts

**OpenClaw uses system Google Chrome** (`/Applications/Google Chrome.app`) in `--headless=new` mode — NOT Playwright Chromium. The Playwright Chromium at `~/Library/Caches/ms-playwright/chromium-1208/` is a separate binary.

The browser launch sequence (in `chrome-BNSd7Bie.js`):
1. `ensurePortAvailable(18800)` — verify port is free
2. `resolveBrowserExecutable()` — finds system Chrome (NOT Playwright)
3. Create user-data dir at `~/.openclaw/browser/openclaw/user-data/`
4. `ensureProfileCleanExit()` — patches `Default/Preferences` but does NOT remove Singleton files
5. Spawn Chrome with `--remote-debugging-port=18800 --headless=new --password-store=basic`
6. Poll `http://127.0.0.1:18800/json/version` every 200ms for 15 seconds
7. If not reachable, SIGKILL Chrome and throw error

## Three Root Causes

### Cause 1: Stale SingletonLock files (most common)
When the gateway gets SIGTERM'd by launchd, Chrome may not be cleanly killed. The lock files persist and prevent a new Chrome from using the same profile directory.

**Diagnosis**: Check for `~/.openclaw/browser/openclaw/user-data/SingletonLock` — the PID in the lock file is not running.

**Fix**:
```bash
rm -f ~/.openclaw/browser/openclaw/user-data/SingletonLock
rm -f ~/.openclaw/browser/openclaw/user-data/SingletonCookie
rm -f ~/.openclaw/browser/openclaw/user-data/SingletonSocket
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

### Cause 2: Corrupted profile with encrypted Keychain tokens (hardest to diagnose)
When Chrome runs in a visible GUI session (e.g., during Amazon re-auth) and the user signs into Google or an encrypted token gets stored, those tokens are encrypted with the macOS Keychain. The headless gateway can't access the Keychain (especially over SSH where `errKCInteractionNotAllowed` occurs). Chrome crashes fatally when it can't decrypt these tokens.

**Diagnosis**: Run Chrome manually with the profile:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --remote-debugging-port=18802 \
  --user-data-dir=/Users/dbochman/.openclaw/browser/openclaw/user-data \
  --no-first-run --password-store=basic about:blank 2>&1
```
If you see `Failed to decrypt token for service AccountId-*` followed by `Trace/BPT trap`, this is the cause.

**Fix**: Replace the corrupted profile:
```bash
launchctl bootout gui/$(id -u)/ai.openclaw.gateway
pkill -f "Google Chrome"
mv ~/.openclaw/browser/openclaw/user-data ~/.openclaw/browser/openclaw/user-data.broken
mkdir -p ~/.openclaw/browser/openclaw/user-data
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
```

**Prevention**: When doing visible browser re-auth, do NOT sign into Google accounts — only sign into the target site (Amazon, etc).

### Cause 3: Orphaned Chrome process on port 18800
A previous Chrome instance is still running and occupying the CDP port.

**Diagnosis**: `lsof -i :18800`

**Fix**: `kill <PID>` then restart gateway.

## Verification
After applying any fix:
```bash
# Wait for gateway to start
sleep 5
# Check browser service is ready
tail -5 ~/.openclaw/logs/gateway.log | grep "Browser control"
# Test Chrome can start (optional direct test)
curl -s http://127.0.0.1:18800/json/version
```

## Notes
- `ensureProfileCleanExit` only patches Preferences — it's an OpenClaw bug that it doesn't clean Singleton files
- A fresh profile has zero cookies — all site logins will need to be redone
- The gateway normally runs under launchd in the GUI session (gui/501), which has Keychain access — the crash happens when Chrome tries to decrypt tokens that were stored in a previous session or when the Keychain is locked
- Testing from SSH always fails with Keychain errors but fresh profiles survive them; corrupted profiles with stored Google account tokens do not
- **Re-auth MUST use system Chrome, NOT Playwright Chromium.** OpenClaw uses `/Applications/Google Chrome.app` — Playwright Chromium (`Google Chrome for Testing`) has a different cookie encryption key, so cookies set in Playwright are unreadable by system Chrome. Always re-auth with: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --user-data-dir=~/.openclaw/browser/openclaw/user-data --password-store=basic`
