---
name: 1password-cli-launchd-hang
description: |
  Fix 1Password CLI (op read) hanging indefinitely in macOS launchd/headless contexts,
  and fix incessant TCC "op would like to access data" popups on macOS Tahoe.
  Use when: (1) A LaunchAgent wrapper script calls `op read` and the service never starts,
  (2) `op read` works over SSH but hangs when run by launchd, (3) Setting
  OP_BIOMETRIC_UNLOCK_ENABLED=true or OP_SERVICE_ACCOUNT_TOKEN doesn't help under launchd,
  (4) Wrapper script appears to "exit silently" because op read blocks forever,
  (5) Repeated "op would like to access data from other apps" TCC popups on macOS Tahoe
  that don't persist after clicking Allow.
  Covers the cache-only pattern (recommended) and probe-timeout-cache pattern for
  reliable secret loading in headless services.
author: Claude Code
version: 1.1.0
date: 2026-02-15
---

# 1Password CLI Hangs Under macOS LaunchAgent

## Problem

`op read` (1Password CLI) hangs indefinitely when called from a macOS LaunchAgent, even with
`OP_SERVICE_ACCOUNT_TOKEN` set. The service account token works fine over SSH or in interactive
terminals, but under launchd the `op` process blocks forever — likely waiting for a Security
framework session or keychain access that launchd processes don't have.

This causes wrapper scripts that fetch secrets via `op read` to never reach the actual service
startup, making it look like the service "exits silently" (launchd reports exit code 0 from
the previous run while the current one hangs).

## Context / Trigger Conditions

- LaunchAgent plist runs a wrapper script that calls `op read`
- Service shows exit code 0 in `launchctl list` but no process in `ps`
- No output in stdout/stderr logs (the script hangs before reaching the service)
- `op read` works fine when you SSH into the same machine
- Setting `OP_BIOMETRIC_UNLOCK_ENABLED=true` makes it worse (waits for desktop app)
- Setting `OP_SERVICE_ACCOUNT_TOKEN` doesn't fix it under launchd
- The wrapper has `2>/dev/null` on `op read`, masking the hang

## Solution

Use a **probe-timeout-cache** pattern:

1. **Cache secrets** in plaintext files on disk (updated when `op read` succeeds)
2. **Probe once** with a background process + kill timer to detect if `op` works
3. **If probe succeeds**, use `op read` for all secrets and refresh cache
4. **If probe hangs**, kill it and read all secrets from cache

### Wrapper Script Pattern

```bash
#!/bin/bash
export HOME="/Users/username"
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"

LOG="$HOME/.myservice/logs/wrapper.log"
echo "$(date -u +%FT%TZ) wrapper starting (pid=$$)" >> "$LOG"

# Load service account token
SA_TOKEN_FILE="$HOME/.myservice/.env-token"
if [[ -f "$SA_TOKEN_FILE" ]]; then
  export OP_SERVICE_ACCOUNT_TOKEN=$(cat "$SA_TOKEN_FILE")
fi

CACHE_DIR="$HOME/.cache/myservice-secrets"
mkdir -p "$CACHE_DIR"

# Probe whether op read works (launchd blocks forever)
OP_AVAILABLE=false
if [[ -n "$OP_SERVICE_ACCOUNT_TOKEN" ]] && command -v op &>/dev/null; then
  tmpfile=$(mktemp)
  op read "op://MyVault/MySecret/password" > "$tmpfile" 2>/dev/null &
  probe_pid=$!
  i=0
  while kill -0 "$probe_pid" 2>/dev/null && [[ $i -lt 3 ]]; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$probe_pid" 2>/dev/null; then
    kill "$probe_pid" 2>/dev/null || true
    echo "$(date -u +%FT%TZ) op read probe timed out - using cache" >> "$LOG"
  else
    wait "$probe_pid" 2>/dev/null || true
    probe_val=$(cat "$tmpfile" 2>/dev/null || true)
    if [[ -n "$probe_val" ]]; then
      OP_AVAILABLE=true
      printf "%s" "$probe_val" > "$CACHE_DIR/my_secret"
    fi
  fi
  rm -f "$tmpfile"
fi

# Read secret: op if available, otherwise cache
_secret() {
  local op_path="$1" cache_file="$2"
  if [[ "$OP_AVAILABLE" == "true" ]]; then
    local val
    val=$(op read "$op_path" 2>/dev/null) || true
    if [[ -n "$val" ]]; then
      printf "%s" "$val" > "$cache_file"
      printf "%s" "$val"
      return
    fi
  fi
  if [[ -f "$cache_file" ]]; then
    cat "$cache_file"
  fi
}

export MY_API_KEY=$(_secret "op://MyVault/API Key/password" "$CACHE_DIR/api_key")

exec /path/to/my/service
```

### Key Design Decisions

- **Single probe, not per-secret**: Avoids 5s * N timeout on every restart
- **Background + poll**: `op read &` + kill timer. Don't use `$()` subshell capture with
  backgrounding — it doesn't reliably capture stdout from `&` processes
- **Temp file for output**: Write `op read` output to a temp file, read it back after wait
- **Cache as primary**: In practice the cache is always used under launchd; op read is just
  for refreshing cache (e.g., after key rotation, when invoked via SSH)
- **`|| true` everywhere**: Prevent `set -e` from aborting on expected failures

### Why Other Approaches Don't Work

| Approach | Why It Fails |
|----------|-------------|
| `OP_BIOMETRIC_UNLOCK_ENABLED=true` | Desktop app can't prompt for biometric under launchd |
| `OP_SERVICE_ACCOUNT_TOKEN` alone | `op` CLI still needs some session context launchd lacks |
| `perl -e 'alarm N; exec @ARGV' -- op read` | `op` may ignore/block SIGALRM |
| `timeout` / `gtimeout` command | Not available on stock macOS |
| Subshell capture: `val=$(op read ... &; wait $!)` | Doesn't capture stdout from background process |

## Verification

After deploying the wrapper:

```bash
# Clear logs and restart
launchctl unload ~/Library/LaunchAgents/my.service.plist
> ~/.myservice/logs/wrapper.log
launchctl load ~/Library/LaunchAgents/my.service.plist
sleep 8

# Check wrapper log for probe result
cat ~/.myservice/logs/wrapper.log
# Expected: "op read probe timed out - using cache" then "secrets loaded" then "exec-ing"

# Verify service is running
lsof -i :PORT
```

## Root Cause (Deep Dive)

The 1Password desktop app registers a **Mach bootstrap service** on macOS. When the `op` CLI
starts, it spawns an `op daemon --background` process that connects to the desktop app via
this Mach port — this is a macOS-specific IPC mechanism that cannot be bypassed by environment
variables, `--config` flags, or socket manipulation.

Under launchd, the `op` daemon connects to the desktop app but the app requires user
interaction (Touch ID/GUI prompt) that can't happen in a non-GUI launchd context. The daemon
blocks waiting for the app to respond, and the CLI blocks waiting for the daemon.

### What We Tested and Ruled Out

| Approach | Result |
|----------|--------|
| `OP_BIOMETRIC_UNLOCK_ENABLED=false` | Still hangs — Mach port connection precedes env check |
| `OP_SERVICE_ACCOUNT_TOKEN` set | Still hangs — daemon spawns before token is evaluated |
| `--config /isolated/dir` | Still hangs — new daemon spawns in isolated dir, same behavior |
| `unset SSH_AUTH_SOCK` | Still hangs — not using SSH agent for IPC |
| `XDG_CONFIG_HOME` override | Still hangs — op doesn't use XDG for daemon |
| Disable "Integrate with CLI" in 1P settings | Still hangs — Mach service still registered |
| Kill `op daemon` + remove socket | New daemon auto-spawns on next `op` invocation |
| `env -i` minimal environment | Still hangs — Mach ports are per-user-session, not env-based |

The **only** reliable workarounds are:
1. **Cache-only pattern** (recommended for Tahoe): Never call `op` from the wrapper at all — read secrets exclusively from cache files. Refresh manually via SSH when needed.
2. **Probe-timeout-cache pattern**: Probe once with a kill timer, fall back to cache. Works but still triggers TCC popups on macOS Tahoe (see below).
3. Ensuring the 1Password desktop app is not running on the machine at all.

## macOS Tahoe TCC Popup Issue

On macOS 26 (Tahoe), even the probe-timeout-cache pattern causes problems. The `op` CLI
triggers a "op would like to access data from other apps" TCC (Transparency, Consent, and
Control) popup every time it runs under launchd. Clicking "Allow" does NOT persist — the
permission resets on every gateway restart, causing an incessant popup storm.

### Symptoms
- Repeated "op would like to access data from other apps" popups on Mac Mini screen
- Clicking "Allow" works momentarily but popup returns on next gateway restart
- Gateway wrapper calls `op read` N times = N popups per restart

### Root Cause
On Tahoe, launchd-spawned processes get a transient TCC session. `op` CLI's data access
permission grant doesn't persist across process restarts because launchd creates a new
security session each time. This is different from pre-Tahoe behavior where TCC grants
for launchd services were sticky.

### Solution: Cache-Only Pattern (Recommended for Tahoe)
Remove ALL `op` calls from the gateway wrapper. Read secrets exclusively from a cache file.

**Preferred pattern** — single KEY=VALUE file sourced with `set -a`:

```bash
#!/bin/bash
export HOME="/Users/dbochman"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

CACHE="$HOME/.openclaw/.secrets-cache"

# Source secrets from cache file (KEY=VALUE format, one per line).
if [[ -f "$CACHE" ]]; then
  set -a
  source "$CACHE"
  set +a
else
  echo "FATAL: No secrets cache at $CACHE" >&2
  exit 1
fi

if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  echo "FATAL: OPENCLAW_GATEWAY_TOKEN not found in cache" >&2
  exit 1
fi

exec /path/to/node /path/to/openclaw/dist/entry.js gateway --port 18789
```

**Cache file format** (`~/.openclaw/.secrets-cache`, chmod 600):
```
OPENAI_API_KEY=sk-proj-...
ELEVENLABS_API_KEY=...
OPENCLAW_GATEWAY_TOKEN=...
BLUEBUBBLES_PASSWORD=...
```

**Refresh helper** (`~/bin/openclaw-refresh-secrets`):
```bash
#!/bin/bash
set -euo pipefail
export OP_SERVICE_ACCOUNT_TOKEN=$(cat "$HOME/.openclaw/.env-token")
CACHE="$HOME/.openclaw/.secrets-cache"
TMP=$(mktemp)
{
  echo "OPENAI_API_KEY=$(op read 'op://OpenClaw/OpenAI API Key/password')"
  echo "ELEVENLABS_API_KEY=$(op read 'op://OpenClaw/ElevenLabs API Key/password')"
  echo "OPENCLAW_GATEWAY_TOKEN=$(op read 'op://OpenClaw/OpenClaw Gateway Token/password')"
} > "$TMP"
mv "$TMP" "$CACHE"
chmod 600 "$CACHE"
echo "Secrets cached to $CACHE"
```

Run over SSH when needed: `ssh dylans-mac-mini ~/bin/openclaw-refresh-secrets`

**Why `set -a; source` over individual `_cached()` calls:**
- Single file = atomic refresh (mv is atomic)
- `set -a` auto-exports all sourced vars — no per-variable boilerplate
- Standard shell pattern, no functions needed
- Easy to audit: `cat ~/.openclaw/.secrets-cache` shows all secrets

**Important: Kill stale `op daemon` processes.** Previous `op read` attempts each spawn an
`op daemon --background` process. These persist and cause TCC popup storms on Tahoe:
```bash
killall op 2>/dev/null  # Clean up stale daemons after migrating to cache-only
```

## Notes

- Cache files contain plaintext secrets — set `chmod 600` on the cache directory
- When rotating API keys: SSH into the machine, export `OP_SERVICE_ACCOUNT_TOKEN`, run
  `op read` manually to refresh cache files, then restart the service
- The 1Password service account must have access to the vault (check with `op vault list`)
- Use `op://VaultName/...` not `op://Private/...` — service accounts typically can't
  access the Private vault
- The `set -e` bash option interacts badly with background process patterns — avoid it or
  use `|| true` liberally
- macOS `launchd` has a `ThrottleInterval` that delays restarts after crashes (default 10s).
  Rapid crash-loop + throttle can make the service appear "dead" even with `KeepAlive: true`
- If the 1Password desktop app is NOT running on the machine, `op read` with
  `OP_SERVICE_ACCOUNT_TOKEN` works instantly under launchd — the hang only occurs when
  the desktop app is present and its Mach bootstrap service is registered

## References

- [1Password CLI App Integration](https://developer.1password.com/docs/cli/app-integration/)
- [1Password Service Accounts with CLI](https://developer.1password.com/docs/service-accounts/use-with-1password-cli/)
- [CLI hangs when requesting items](https://1password.community/discussion/139010/cli-hangs-when-requesting-items)
