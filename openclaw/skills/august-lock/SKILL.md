---
name: august-lock
description: Safely control and verify the August smart lock at Crosstown. Lock, explicitly confirm unlocks, and check status (locked/unlocked, door open/closed, battery). Use when asked to lock or unlock the door, check whether the door is locked, or check lock battery.
allowed-tools: Bash(august:*)
metadata: {"openclaw":{"emoji":"🔒","requires":{"bins":["august"]}}}
---

# August Smart Lock — Crosstown

Control the August Wi-Fi Smart Lock (4th gen) at **Crosstown (Crosstown residence)** via the August cloud API.

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

The command first verifies that the door is closed and refuses to extend the
bolt otherwise. If it is already locked and closed, it returns a verified
no-op. After a real action it polls status up to five times and succeeds only
after the lock reports **locked** and the door reports **closed**.

### Unlock the door

First run a read-only preview:

```bash
august unlock [lock-id]
```

The preview verifies an exact, unambiguous lock ID, lock state, and door state,
then returns a short-lived `approval_id`. It does not call the unlock API. Show
the observed facts to the user and ask for explicit confirmation of that exact
unlock. After confirmation, consume that approval:

```bash
august unlock --confirm <approval-id>
```

Approvals expire after about five minutes and can be used only once. Confirming
atomically consumes the approval, rechecks that the lock ID and observed lock
and door states have not changed, and then makes at most one remote unlock
call. Changed facts, expiry, malformed state, or replay fail before mutation.
After a real action the remote command polls status up to five times and
succeeds only after the lock reports **unlocked** with a recognized open/closed
door state.

Status, lock, unlock preview, and details accept an optional August lock ID.
Lock IDs must be exactly 32 hexadecimal characters.

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
- The wrapper accepts only documented commands, validates codes and lock IDs,
  and sends argv as base64-encoded JSON so input is never interpolated into the
  remote shell command
- Unlock approvals are held only on the Mini in an owner-only mode-`0700`
  cache with atomic mode-`0600` state files; the bearer ID is bound to the
  previewed lock identity and physical state
- Auth is resolved only on the MBP from its protected config; the Mini never
  forwards an account identifier or password over SSH

## Initial Setup (one-time)

The first time, you need to complete 2FA:

```bash
august authorize           # Sends a 6-digit code to the configured account
august validate <code>     # Exactly 6 digits; saves installId for future use
```

Before authorization, provision `augustId` and `password` directly in
`~/.openclaw/august/config.json` on the MBP using the attended 1Password
workflow. Never pass either value as a CLI argument or copy the Mini's cache.
The file must be owned by that user, be a regular non-symlink file, and have
mode `0600`. After validation, `installId` is saved back to that same protected
file. No further 2FA is normally needed until the token expires.

## Auth

- Account and password: `augustId` and `password` fields in the protected MBP
  config; environment variables are an attended MBP-only fallback
- Config: `~/.openclaw/august/config.json` on MBP (credentials and installId), required mode `0600`
- Token: JWT, auto-refreshed, ~120 day expiry

Config updates use a mode-`0600` temporary file, `fsync`, and atomic rename.
Malformed, symlinked, non-regular, or incorrectly permissioned config fails
closed and is never replaced automatically. Save failures exit nonzero.

## Safety rules

- Run `status` freely; it is read-only.
- Run `lock` only when requested or as part of an already authorized routine.
- For `unlock`, run the preview, show its exact observed facts, obtain explicit
  user confirmation, and use only its returned approval ID. Never infer
  confirmation from presence, a doorbell event, or an earlier request.
- Never bypass the local `august` wrapper with raw SSH or call the remote
  mutation command directly.
- An approval is consumed before its state recheck. If confirmation fails,
  expires, or reports changed facts, request a fresh preview instead of retrying
  the same ID.
- Treat `unlock_outcome_unknown` as non-retryable: the single mutation may have
  reached the lock. Reconcile physical state directly before any new request.
- Treat any other nonzero result or `verification_failed` as an unconfirmed
  physical state and tell the user to check the lock directly.
- Do not loosen lock-ID, auth-code, config-permission, or SSH argv validation to
  work around an error.

## Files

| File | Path |
|------|------|
| CLI entrypoint | `~/.openclaw/bin/august` (delegates to the deployed skill copy) |
| Protected wrapper | `~/.openclaw/skills/august-lock/august` |
| Approval helper | `~/.openclaw/skills/august-lock/august-approval.py` |
| Approval cache | `~/.cache/openclaw-gateway/august-unlock-approvals/` (Mini only) |
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
