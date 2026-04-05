---
name: august-lock
description: Control the August smart lock at Crosstown. Lock, unlock, check status (locked/unlocked, door open/closed, battery). Use when asked to lock or unlock the door, check if the door is locked, or check lock battery.
allowed-tools: Bash(august:*)
metadata: {"openclaw":{"emoji":"🔒","requires":{"bins":["august"]}}}
---

# August Smart Lock — Crosstown

Control the August Wi-Fi Smart Lock (4th gen) at **Crosstown (19 Crosstown Ave)** via the August cloud API.

## Commands

### Check lock status
```bash
august status
```
Returns JSON with lock state (locked/unlocked), door position (open/closed), and battery level.

### Lock the door
```bash
august lock
```

### Unlock the door
```bash
august unlock
```

### List locks on account
```bash
august locks
```

### Full lock details
```bash
august details
```

## Architecture

```
August Lock ←─WiFi─→ August Cloud API ←─HTTPS─→ august-cmd.js (MBP) ←─SSH─→ august CLI (Mini)
```

- `august-cmd.js` on the MacBook Pro uses the `august-api` npm package to talk to August's cloud API
- The CLI SSHs to the MBP for each command (same pattern as crosstown-roomba)
- Auth: August account (dylanbochman@gmail.com), JWT token cached after 2FA

## Initial Setup (one-time)

The first time, you need to complete 2FA:

```bash
august authorize           # Sends 6-digit code to dylanbochman@gmail.com
august validate <code>     # Enter the code — saves installId for future use
```

After validation, the `installId` is saved to `~/.openclaw/august/config.json` on the MBP. No further 2FA needed unless the token expires (~120 days).

## Auth

- Account: `dylanbochman@gmail.com`
- Password: `AUGUST_PASSWORD` env var (in `~/.openclaw/.secrets-cache`)
- Config: `~/.openclaw/august/config.json` on MBP (installId + credentials)
- Token: JWT, auto-refreshed, ~120 day expiry

## Files

| File | Path |
|------|------|
| CLI wrapper | `~/.openclaw/bin/august` → `~/.openclaw/skills/august-lock/august` |
| Node.js cmd | `~/.openclaw/august/august-cmd.js` (on MBP) |
| npm package | `~/.openclaw/august/node_modules/august-api/` (on MBP) |
| Config | `~/.openclaw/august/config.json` (on MBP) |
| Symlink | `/opt/homebrew/bin/august` → `~/.openclaw/bin/august` |

## Skill Boundaries

This skill controls the August lock at **Crosstown only**.

For related tasks, switch to:
- **crosstown-routines**: Full routines (away, goodnight) that may include locking
- **presence**: Check if anyone is home before locking/unlocking
- **ring-doorbell**: Check who's at the door before unlocking
