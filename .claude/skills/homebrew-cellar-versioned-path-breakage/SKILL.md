---
name: homebrew-cellar-versioned-path-breakage
description: |
  Fix long-lived services (LaunchAgents, cron wrappers, systemd-on-Linuxbrew
  units, PATH-prefixing scripts) that crash silently after a routine `brew
  upgrade` because they hardcode a versioned Cellar path like
  `/opt/homebrew/Cellar/node@22/22.22.0/bin/node`. Use when: (1) a service
  that ran fine for months suddenly crash-loops with no code changes,
  (2) `launchctl list <label>` shows `PID = "-"` and
  `LastExitStatus = 32256` (or any multiple of 256 — that's a wait(2)
  status word; divide by 256 for the actual exit code, here 126 =
  "command found but not executable / No such file or directory"),
  (3) the service's stderr log shows `bash: line N:
  /opt/homebrew/Cellar/<formula>/<version>/bin/<binary>: No such file or
  directory` repeating on every launchd respawn, (4) downstream symptoms
  appear far from the cause (e.g., dashboards showing "cron overdue", a
  webhook endpoint returning connection-refused, scheduled jobs not
  firing) because the upstream service is silently dead. The fix is to
  replace `/opt/homebrew/Cellar/<formula>/<version>/...` with
  `/opt/homebrew/opt/<formula>/...` — Homebrew atomically repoints the
  `opt/` symlink on every upgrade, so it survives version bumps. Applies
  on both Apple Silicon (`/opt/homebrew`) and Intel/Linuxbrew
  (`/usr/local`, `/home/linuxbrew/.linuxbrew`) — same Cellar/opt
  convention.
author: Claude Code
version: 1.0.0
date: 2026-04-30
---

# Homebrew Cellar versioned-path breakage in long-lived services

## Problem

A LaunchAgent, cron wrapper, or systemd unit hardcodes a path under
`/opt/homebrew/Cellar/<formula>/<exact-version>/bin/<binary>`. Everything
works fine until the next `brew upgrade <formula>` (which can happen
unattended via `brew autoupdate`, a Mas update, or even a side-effect of
upgrading something else). Brew installs the new version to a different
Cellar dir and removes the old one. The hardcoded path now points at a
deleted directory; every subsequent launchd respawn crashes with
`No such file or directory`. The service silently disappears.

The damage often surfaces hours or days later as a downstream symptom
(missed cron, dead webhook, dashboard alert) with no obvious connection
to the upgrade. The service may have been crash-looping the entire time,
exhausting launchd's throttle and going dormant.

## Context / Trigger Conditions

Any of these together strongly indicate this issue:

1. **Silent service death**: `launchctl list <label>` shows `PID = "-"`
   (not running) and a recent non-zero `LastExitStatus`.
2. **`LastExitStatus = 32256`**: that's `126 << 8`, the wait(2) status
   word for "command not executable / file not found". General rule:
   any non-zero `LastExitStatus` that's a multiple of 256 → divide by
   256 to get the bash exit code (`32256/256 = 126`, `127*256 = 32512`
   for "command not found", etc.).
3. **stderr log shows path errors**: tail the path in the plist's
   `StandardErrorPath`. Look for repeated lines like:
   ```
   /path/to/wrapper: line N: /opt/homebrew/Cellar/<formula>/<old-version>/bin/<binary>: No such file or directory
   /path/to/wrapper: line N: exec: /opt/homebrew/Cellar/<formula>/<old-version>/bin/<binary>: cannot execute: No such file or directory
   ```
4. **Downstream symptoms with no nearby cause**: cron jobs marked overdue,
   API endpoints down, scheduled tasks not firing, channel/webhook
   provider stops delivering — and `git log`/recent changes show nothing
   that would explain it.
5. **Recent Homebrew activity**: `ls -lt /opt/homebrew/Cellar/<formula>/`
   shows a directory mtime hours-to-days before the symptom started.
   Cross-check with `brew log <formula>` if available.

## Solution

### Diagnostic chain

```bash
# 1. Confirm service isn't running and capture exit status
launchctl list <label> | grep -E "PID|LastExitStatus"
# Expect: PID = "-", LastExitStatus = some non-zero multiple of 256

# 2. Decode the exit status (divide by 256 for shell exit code)
#    32256 / 256 = 126 → "found but not executable" (path missing)
#    32512 / 256 = 127 → "command not found"
#    Other values: bitwise unpack with `WEXITSTATUS` semantics

# 3. Find stderr path from the plist or launchctl
launchctl list <label>  # look for StandardErrorPath
tail -50 <stderr-path>

# 4. Identify the broken hardcoded path (will be /opt/homebrew/Cellar/<formula>/<old-version>/...)

# 5. Confirm the new Cellar version exists (proves a recent upgrade)
ls /opt/homebrew/Cellar/<formula>/

# 6. Confirm the stable opt/ symlink works
ls -la /opt/homebrew/opt/<formula>/bin/<binary>
```

### Fix

Replace the versioned Cellar path with the stable opt/ path **everywhere**
it appears (wrapper scripts, plist `Program`/`ProgramArguments`, env vars,
PATH prefixes):

```bash
# Before:
exec /opt/homebrew/Cellar/node@22/22.22.0/bin/node /path/to/app.js

# After:
exec /opt/homebrew/opt/node@22/bin/node /path/to/app.js
```

`/opt/homebrew/opt/<formula>` is itself a symlink that Homebrew updates
atomically on each upgrade (it points at whichever Cellar dir is current).
That makes it stable across version bumps. The same convention applies on
Linuxbrew (`/home/linuxbrew/.linuxbrew/opt/<formula>`) and Intel macOS
Homebrew (`/usr/local/opt/<formula>`).

### Restart and verify

```bash
# Truncate stderr (otherwise it fills with retries and obscures fresh output)
: > <stderr-path>

# Kickstart the service (gui/<uid> for LaunchAgents, system/ for LaunchDaemons)
launchctl kickstart -k gui/$(id -u)/<label>

# Verify it stays up
sleep 3
launchctl list <label> | grep -E "PID|LastExitStatus"
# Expect: PID = <some number>, LastExitStatus = 0
```

## Verification

- `launchctl list <label>` shows a real `PID` and `LastExitStatus = 0`.
- stderr log stops accumulating "No such file or directory" lines.
- Downstream symptom (the original alert) clears on the next scheduled run.
- `ls -la /opt/homebrew/opt/<formula>/bin/<binary>` resolves to a real
  binary — confirms the fix points at something Homebrew maintains.

## Example

A LaunchAgent wrapper at `~/Applications/MyService.app/Contents/MacOS/MyService`:

```bash
#!/bin/bash
# ... env setup, secrets loading, log rotation ...

# Broken (hardcoded version, breaks on every brew upgrade of node@22):
exec /opt/homebrew/Cellar/node@22/22.22.0/bin/node /opt/homebrew/lib/node_modules/myservice/dist/index.js

# Fixed:
exec /opt/homebrew/opt/node@22/bin/node /opt/homebrew/lib/node_modules/myservice/dist/index.js
```

Note: `/opt/homebrew/lib/node_modules/...` is also a stable opt-style
location (it's `/opt/homebrew/lib`, not `/opt/homebrew/Cellar/<v>/lib`),
so that part is fine.

## Notes

- **Audit other wrappers proactively**: once you find one Cellar-versioned
  path, grep for the pattern across all your launchd plists and wrapper
  scripts:
  ```bash
  grep -rn "/opt/homebrew/Cellar" ~/Library/LaunchAgents \
    ~/Applications/*.app/Contents/MacOS/ ~/.openclaw ~/bin 2>/dev/null
  ```
- **Don't use `which <binary>` output in scripts** — `which` resolves to
  the current Cellar path on some PATH configurations, so saving its
  output bakes in the same problem.
- **`brew --prefix <formula>` is also stable** if you prefer it to
  hardcoded `/opt/homebrew/opt/<formula>` (e.g., for cross-platform
  scripts). It returns the opt-symlink path. Slight tradeoff: shells out
  to brew, so adds startup latency.
- **Versioned formulas are still safe to use** (e.g., `node@22` vs `node`)
  — the Homebrew opt symlink is per-formula, so `/opt/homebrew/opt/node@22`
  always points to the latest 22.x Cellar dir while `/opt/homebrew/opt/node`
  tracks latest stable. Pin the formula in your Brewfile, not in the path.
- **Why launchd makes this look mysterious**: failed wrappers crash fast,
  launchd respawns them, they crash again. After ~10 rapid crashes
  launchd throttles for 10s, then resumes. You can have hundreds of
  identical "No such file or directory" lines per minute in stderr but
  no single "service failed" alert anywhere — the only signal is
  downstream (dashboards, missed jobs, dead endpoints).
- **Related upgrade hazards on this same stack**: `npm install -g <pkg>`
  may overwrite a custom LaunchAgent plist if the package's post-install
  hook calls `<pkg> install --service` — see also
  `openclaw-upgrade-plist-overwrite` skill.

## References

- Homebrew docs on Cellar layout: <https://docs.brew.sh/Formula-Cookbook#cellar>
- `launchctl(1)` — `LastExitStatus` is the wait(2) status word
- `bash(1)` exit codes 126 (not executable), 127 (not found)
