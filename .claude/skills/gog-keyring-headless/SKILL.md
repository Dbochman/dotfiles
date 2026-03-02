---
name: gog-keyring-headless
description: |
  Fix gog (gogcli) Google Workspace CLI failing in headless/SSH/launchd contexts
  with "no TTY available for keyring file backend password prompt; set GOG_KEYRING_PASSWORD".
  Use when: (1) gog commands fail with no-TTY keyring prompt error over SSH or in launchd,
  (2) GOG_KEYRING_PASSWORD is set but gives "aes.KeyUnwrap(): integrity check failed"
  (wrong password), (3) Need to re-auth gog accounts after losing the file keyring password,
  (4) Setting up gog for headless use on Mac Mini or CI. Covers the file keyring encryption
  model, re-auth flow, and 1Password cache pattern for GOG_KEYRING_PASSWORD.
author: Claude Code
version: 1.0.0
date: 2026-02-09
---

# gog (gogcli) Headless Keyring Fix

## Problem
gog uses a file-based encrypted keyring (JWE with PBES2-HS256+A128KW) to store OAuth
refresh tokens. In headless contexts (SSH, launchd, no-TTY), it cannot prompt for the
keyring password and fails.

## Context / Trigger Conditions
- **Error**: `no TTY available for keyring file backend password prompt; set GOG_KEYRING_PASSWORD`
- **Error**: `aes.KeyUnwrap(): integrity check failed` (wrong password)
- Running gog over SSH to a headless Mac
- OpenClaw gateway trying to invoke gog commands
- Any non-interactive context (CI, cron, launchd)

## Key Paths
- Config: `~/Library/Application Support/gogcli/config.json`
- Keyring tokens: `~/Library/Application Support/gogcli/keyring/token:<account>`
- Binary: `/opt/homebrew/bin/gog` (installed via `brew install steipete/tap/gogcli`)

## Solution

### If password is known
Just set the env var:
```bash
export GOG_KEYRING_PASSWORD='your-password'
gog calendar events --today
```

### If password is lost (integrity check failed)
Must re-auth — tokens are encrypted and unrecoverable without the password:

1. Delete old tokens:
   ```bash
   GOG_KEYRING_PASSWORD=dummy gog auth tokens delete <email> --force
   ```

2. Generate a new password and store it:
   ```bash
   # Generate
   openssl rand -base64 24 | tr -d '/+=' | head -c 24

   # Store in 1Password
   op item create --category=password --title="GOG CLI" --vault="OpenClaw" \
     "password=<generated>" "notesPlain=GOG file keyring password"

   # Cache for headless use
   echo -n '<generated>' > ~/.cache/openclaw-gateway/gog_keyring_password
   chmod 600 ~/.cache/openclaw-gateway/gog_keyring_password
   ```

3. Re-add accounts (requires browser for OAuth):
   ```bash
   export GOG_KEYRING_PASSWORD='<generated>'
   gog auth add dylanbochman@gmail.com
   gog auth add juliajoyjennings@gmail.com
   ```

4. Wire into OpenClaw gateway wrapper:
   ```bash
   # Add to OpenClawGateway.app/Contents/MacOS/OpenClawGateway:
   export GOG_KEYRING_PASSWORD=$(_secret "op://OpenClaw/GOG CLI/password" "$CACHE_DIR/gog_keyring_password")
   ```

### Alternative: export/import tokens
If gog is authed on a machine with a browser, export tokens and import on headless:
```bash
# On machine with browser:
GOG_KEYRING_PASSWORD='pwd' gog auth tokens export user@gmail.com --out /tmp/token.json

# On headless machine:
GOG_KEYRING_PASSWORD='pwd' gog auth tokens import /tmp/token.json
```

## Verification
```bash
GOG_KEYRING_PASSWORD=$(cat ~/.cache/openclaw-gateway/gog_keyring_password) \
  gog calendar events --today --no-input
```
Should return events (or "No events") without errors.

## Notes
- The `--no-input` flag prevents gog from prompting for anything (good for headless)
- Token files are JWE-encrypted — you CANNOT decrypt them without the original password
- The `keychain` backend alternative uses macOS Keychain, which has its own headless issues
- After updating the gateway wrapper, restart with: `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`
- 1Password ref: `op://OpenClaw/GOG CLI/password`
