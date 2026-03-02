---
name: chrome-remote-debugging-setup
description: |
  Fix Chrome --remote-debugging-port silently failing to open any port. Use when:
  (1) Chrome launches with --remote-debugging-port=9222 but curl to 127.0.0.1:9222
  returns "exit code 7" (connection refused), (2) lsof shows no LISTEN on the
  debugging port despite Chrome having the flag in its process args, (3) Setting up
  chrome-devtools-mcp or any CDP-based tool with a real Chrome instance. The root
  cause is that Chrome requires --user-data-dir when --remote-debugging-port is used,
  and silently ignores the port flag without it (error only visible on stderr).
author: Claude Code
version: 1.0.0
date: 2026-02-15
---

# Chrome Remote Debugging Port Setup

## Problem
Chrome silently ignores `--remote-debugging-port` unless `--user-data-dir` is also
specified. No error appears in the browser UI — the flag is simply dropped. The actual
error message ("DevTools remote debugging requires a non-default data directory") only
appears on stderr when Chrome is launched from a terminal.

## Context / Trigger Conditions
- `curl -s http://127.0.0.1:9222/json/version` returns connection refused
- `lsof -i :9222` shows nothing listening despite Chrome running with the flag
- `pgrep -fl "remote-debugging"` confirms the flag is in Chrome's process args
- Trying to connect chrome-devtools-mcp, Puppeteer, or any CDP client to a real Chrome

## Solution

### 1. Launch Chrome with both flags

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-debug-profile"
```

Or for Chrome Canary:
```bash
/Applications/Google\ Chrome\ Canary.app/Contents/MacOS/Google\ Chrome\ Canary \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-debug-profile"
```

### 2. Important: Chrome must be fully quit first

The `--remote-debugging-port` flag is only honored by the **first** Chrome process.
If Chrome is already running, launching again just opens a new window in the existing
process (ignoring all command-line flags).

```bash
pkill -9 "Google Chrome"  # or "Google Chrome Canary"
sleep 2
# Then launch with flags
```

### 3. Verify the port is live

```bash
curl -s http://127.0.0.1:9222/json/version
```

Should return JSON with Browser, Protocol-Version, webSocketDebuggerUrl fields.

### 4. MCP config for chrome-devtools-mcp

In `.claude/mcp.json`:
```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest", "--no-usage-statistics", "--browserUrl", "http://127.0.0.1:9222"]
    }
  }
}
```

Claude Code must be restarted after changing MCP config.

## Verification
```bash
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool
```

## Notes
- The `--user-data-dir` creates a separate Chrome profile. It won't have your existing
  logins/extensions unless you point it at your real profile directory.
- Chrome Canary can share profiles with stable Chrome but uses a separate default profile.
- Enterprise-managed Chrome (e.g., Groq enrollment) does NOT block remote debugging —
  the missing `--user-data-dir` was the actual cause.
- To diagnose, always launch Chrome from terminal to see stderr output.
- A convenient alias: `alias chrome-debug='...'` in ~/.zshrc

## References
- https://developer.chrome.com/docs/devtools/remote-debugging/
- Chrome source: the requirement was added to prevent debugging the user's default profile
  without explicit opt-in
