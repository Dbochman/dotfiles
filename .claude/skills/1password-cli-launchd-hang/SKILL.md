---
name: 1password-cli-launchd-hang
description: >-
  Diagnose and fix 1Password CLI hangs or repeated TCC prompts in macOS
  LaunchAgent and other unattended contexts by converting the complete process
  tree to a protected cache-only credential contract.
---

# 1Password CLI hangs under launchd

## Symptoms

- A LaunchAgent wrapper stalls before its service starts.
- `op` works in an attended terminal but hangs or opens repeated permission
  prompts under launchd.
- Adding service-account or biometric environment flags does not make the
  unattended path reliable.
- A parent wrapper appears cache-only, but a child CLI eventually falls back to
  `op` after its own cache expires.

## Stable solution

Remove every `op` invocation from the LaunchAgent process tree. Do not probe it
with a timeout, start its daemon, or rely on the desktop app being closed.

1. Source required values from `~/.openclaw/.secrets-cache`.
2. Require the cache to be a regular, owner-owned mode-`0600` file.
3. Check required key names without printing values.
4. Fail closed if the cache is missing, insecure, or incomplete.
5. Audit every child executable for direct or fallback `op` calls.

Example service wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail

CACHE="${OPENCLAW_SECRETS_CACHE:-$HOME/.openclaw/.secrets-cache}"
[[ -f "$CACHE" && ! -L "$CACHE" ]] || exit 1
set -a
source "$CACHE"
set +a
[[ -n "${REQUIRED_KEY:-}" ]] || exit 1
exec service-command
```

Never print or `cat` the cache while diagnosing it.

## Attended refresh

Refresh the cache only from a user-controlled terminal:

```bash
openclaw-refresh-secrets --interactive
```

The tracked helper reads exact field references from an owner-only local seed.
It calls `op read` only in this explicit maintenance flow, requires all fields,
shell-quotes values, fsyncs a protected temporary file, and atomically replaces
the last-good cache. Neither field references nor credential values belong in
tracked source or diagnostic output.

## Verification

- Search the plist program, wrappers, and child CLIs for `op` and 1Password
  environment fallbacks.
- Confirm the cache path is a non-symlink regular file owned by the service user
  with mode `0600`; do not display its content.
- Start the service and verify its real health endpoint or postcondition rather
  than relying only on launchd exit status.
- Confirm no new `op` daemon, GUI prompt, or TCC event appears.

The presence or absence of the desktop app is not part of the service contract;
an unattended service remains cache-only in every case.
