---
name: pinchtab-0.11-upgrade-gotchas
description: |
  Survive the upgrade from pinchtab v0.7.x/v0.8.x → v0.11.x without silent
  breakage. Use when: (1) you ran `npm install -g pinchtab@latest` and every
  CLI invocation errors with "Pinchtab binary not found at:
  ~/.pinchtab/bin/pinchtab-darwin-arm64 ... To fix this, run: npm rebuild
  pinchtab" (and `npm rebuild` doesn't actually fix it), (2) `pinchtab eval`
  or curl POST to `/evaluate` returns "Error 403: evaluate endpoint is
  disabled; enable security.allowEvaluate in config to use this endpoint",
  (3) any downstream automation that hits `pinchtab eval` (e.g. grocery-reorder
  with custom event-dispatch JS for Angular click) silently fails after a
  pinchtab major-version bump, (4) you're auditing a pinchtab profile dir at
  `~/.pinchtab/chrome-profile/` and wondering why the new version ignores it.
  Covers the platform-specific Mach-O binary download, the new
  `security.allowEvaluate` gate, and the profile path migration.
author: Claude Code
version: 1.0.0
date: 2026-05-10
---

# pinchtab v0.11 Upgrade Gotchas

## Problem

The pinchtab v0.11.x release (April–May 2026) introduced three
breaking changes that the npm package's `npm install -g pinchtab@latest`
won't surface as failures, leaving the CLI broken or silently degraded:

1. **Native binary missing after install.** `npm install -g` runs the
   package's `postinstall` hook in some contexts but not all (in this
   session, on a Mini with Homebrew Node 22 + global `node_modules` under
   `/opt/homebrew/lib`, the hook didn't fire). The CLI shim then errors
   on every call because the platform-specific Mach-O binary at
   `~/.pinchtab/bin/<version>/pinchtab-darwin-arm64` was never downloaded.
   The error message suggests `npm rebuild pinchtab`, but rebuild doesn't
   re-trigger the postinstall download either.

2. **`/evaluate` endpoint gated.** `pinchtab eval` (CLI) and POST `/evaluate`
   (HTTP) now return 403 by default. Any automation that uses `eval` for
   custom JS (event dispatch, cookie inspection, page-state polling) breaks
   silently after the upgrade.

3. **Profile path moved.** `~/.pinchtab/chrome-profile/` (single-profile
   layout) → `~/.pinchtab/profiles/<name>/` (multi-profile parent dir).
   In this session the migration appeared to preserve session cookies for
   visited domains, but the old profile dir is left behind and not
   referenced.

## Context / Trigger Conditions

- After `npm install -g pinchtab@latest`, every `pinchtab` invocation prints:
  ```
  ❌ Pinchtab binary not found at:
    ~/.pinchtab/bin/pinchtab-darwin-arm64
  To fix this, run:
    npm rebuild pinchtab
  ```
  And `npm rebuild pinchtab` reports success but doesn't fix it.
- `pinchtab eval "document.title"` returns:
  `Error 403: evaluate endpoint is disabled; enable security.allowEvaluate in config to use this endpoint`
- A script that POSTs to `http://127.0.0.1:9867/evaluate` gets the same 403.
- After upgrade, a fresh `~/.pinchtab/profiles/default/` exists alongside
  the old `~/.pinchtab/chrome-profile/`.

## Solution

Run all three fixes in sequence after `npm install -g pinchtab@latest`:

```bash
# 1. Manually run the postinstall to download the platform-specific binary
NODE_PKG_DIR="$(npm root -g)/pinchtab"
node "$NODE_PKG_DIR/scripts/postinstall.js"
# Expected output:
#   Downloading Pinchtab <version> for darwin-arm64...
#   ✓ Verified and installed: ~/.pinchtab/bin/<version>/pinchtab-darwin-arm64
#   ✓ Pinchtab setup complete

# 2. Re-enable the /evaluate endpoint (required for any custom-JS automation)
pinchtab config set security.allowEvaluate true

# 3. Restart any running pinchtab server so it picks up the config change
#    (`pinchtab daemon stop` only handles the LaunchAgent daemon — auto-spawned
#    background servers from `pinchtab nav` need pkill)
pinchtab daemon stop 2>/dev/null
pkill -f "pinchtab.*server" 2>/dev/null
pkill -f "pinchtab.*bridge" 2>/dev/null
pkill -f "Google Chrome.*pinchtab" 2>/dev/null
sleep 2
```

## Verification

```bash
pinchtab --version    # should show 0.11.x
pinchtab nav https://example.com
pinchtab eval "document.title"   # should print "Example Domain", not 403
```

If the automation depends on a particular site's session cookies (e.g.,
grocery-reorder with Star Market MFA), navigate to that site and check
auth-state cookies are present:

```bash
pinchtab nav https://www.starmarket.com
pinchtab eval "JSON.stringify({hasAuth: document.cookie.includes('SWY_SHARED_SESSION')})"
# Expected: {"hasAuth":true}
```

If `hasAuth` is false, the site session was lost in the profile migration
and you'll need to re-auth (often automated via Gmail MFA in the wrapping
script).

## Example

Snapshot from this session's upgrade:

```
$ npm install -g pinchtab@latest
changed 1 package in 887ms

$ pinchtab --version
❌ Pinchtab binary not found at:
  ~/.pinchtab/bin/pinchtab-darwin-arm64

$ npm rebuild -g pinchtab
rebuilt dependencies successfully
$ pinchtab --version
❌ Pinchtab binary not found at: ...

$ node /opt/homebrew/lib/node_modules/pinchtab/scripts/postinstall.js
Downloading Pinchtab 0.11.0 for darwin-arm64...
✓ Verified and installed: ~/.pinchtab/bin/0.11.0/pinchtab-darwin-arm64
✓ Pinchtab setup complete

$ pinchtab --version
pinchtab 0.11.0

$ pinchtab eval "document.title"
Error 403: evaluate endpoint is disabled; enable security.allowEvaluate
in config to use this endpoint

$ pinchtab config set security.allowEvaluate true
Set security.allowEvaluate = true

$ pkill -f "pinchtab.*server"; sleep 2
$ pinchtab nav https://example.com && pinchtab eval "document.title"
Example Domain
```

## Notes

- **Always backup the profile dir before upgrading**. `cp -a ~/.pinchtab
  ~/.pinchtab.pre-<new-version>` is cheap insurance — the dir is usually
  a few hundred MB and gives a clean rollback path if cookies don't survive.
- **Check the v0.11.0 release notes for other security gates.** The release
  introduced `security.allow*` flags for several capabilities (clipboard,
  state export, network intercept, downloads). If your automation uses
  those, you'll need to enable each one individually.
- **The daemon model is new in v0.11.x.** `pinchtab daemon install` writes
  a LaunchAgent at `~/Library/LaunchAgents/com.pinchtab.pinchtab.plist`.
  In this session, `pinchtab daemon start` failed with
  `launchctl bootstrap gui/501 ... exit status 5: Bootstrap failed: 5`.
  Not investigated — the daemon isn't required for one-shot use, since
  `pinchtab nav` auto-spawns a background server. Skip the daemon unless
  you need pinchtab to survive reboots.
- **The npm postinstall hook may or may not fire** depending on Node
  installation context (Homebrew keg-only vs system Node, npm version,
  global vs local install). Always run the postinstall script manually
  after `npm install -g` for pinchtab — it's idempotent.

## References

- pinchtab releases: https://github.com/pinchtab/pinchtab/releases
- v0.11.0 release notes (network intercept, frame scoping, security
  hardening): https://github.com/pinchtab/pinchtab/releases/tag/v0.11.0
- v0.8.5 release notes (where the `security.allow*` gating model was
  introduced): https://github.com/pinchtab/pinchtab/releases/tag/v0.8.5
