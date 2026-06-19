---
name: macos-openrsync-vs-gnu-rsync
description: >-
  Fix rsync option failures under macOS launchd, cron, or SSH when /usr/bin/rsync is openrsync rather
  than GNU rsync.
author: Claude Code
version: 1.0.0
date: 2026-05-24
---

# macOS `openrsync` vs GNU rsync

## Problem

macOS ships **Apple's `openrsync` as `/usr/bin/rsync`** — a BSD-licensed
reimplementation that identifies itself as "rsync version 2.6.9 compatible"
and rejects any GNU rsync 3.x flag. Scripts that work fine on your
interactive shell (where Homebrew's GNU rsync is on PATH) silently fail when
a LaunchAgent or cron job runs the same script with a stock-macOS PATH.

The error is loud (full usage dump, non-zero exit) but the *context* is
lost: a wrapper with `set -euo pipefail` aborts with the rsync exit code
buried in `~/Library/Logs/...` and no clear signal that the issue is
"wrong rsync binary", not "your data is broken."

## Context / Trigger Conditions

Hit this when:

- `rsync --version` on the host returns `openrsync: protocol version 29`
  (i.e., the stock Apple binary at `/usr/bin/rsync`).
- A backup wrapper fails with one of:
  - `rsync: unrecognized option '--info=stats2'`
  - `rsync: unrecognized option '--info=progress2'`
  - `rsync: unrecognized option '--mkpath'`
  - `rsync: unrecognized option '--debug=del'` (or any `--debug=...`)
  - `rsync: unrecognized option '--out-format=...'`
- The same script works on the same machine in an interactive shell. PATH
  diff is the smoking gun:
  ```bash
  # Interactive (Homebrew on PATH)
  $ which rsync; rsync --version | head -1
  /opt/homebrew/bin/rsync
  rsync  version 3.4.1  protocol version 32
  # Under launchd with default PATH
  $ /bin/launchctl print gui/$UID/com.example.label | grep -A1 PATH
    PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
  ```
- Script ported from a Linux box or a Docker container where GNU rsync is
  standard, dropped into a macOS LaunchAgent without auditing flags.

## Solution

Three approaches, increasing infrastructure cost:

### 1. Stick to the openrsync-compatible flag subset (recommended)

Portable across stock macOS, Linux GNU rsync, and (mostly) Docker rsync. No
extra dependencies, no PATH manipulation:

| Use | Instead of |
|-----|-----------|
| `--stats` | `--info=stats2` |
| `--progress` | `--info=progress2` |
| `-P` (= `--partial --progress`) | same |
| `-v`, `-vv`, `-vvv` | `--info=...` |
| `ssh REMOTE "mkdir -p PATH"` before rsync | `--mkpath` |
| `--exclude=PATTERN` | same |
| `-a -z --delete --stats` | safe core set, all compatible |

### 2. Hardcode the GNU rsync binary path

If you specifically need GNU rsync 3.x features (e.g., `--mkpath` is genuinely
nicer, or `--info=progress2` for human-readable totals):

```bash
# In the wrapper script
RSYNC=/opt/homebrew/bin/rsync   # GNU rsync 3.x via Homebrew
[[ -x "$RSYNC" ]] || { echo "ERROR: GNU rsync not at $RSYNC"; exit 1; }
"$RSYNC" -az --delete --info=progress2 ...
```

Adds a hard dependency on Homebrew. Less portable but fine for personal
infra where you know the host has Homebrew.

### 3. Put Homebrew first in the LaunchAgent's PATH

In the plist's `EnvironmentVariables`:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/local/bin</string>
</dict>
```

Lets `rsync` (and all other Homebrew tools) resolve to the GNU version under
launchd. Slightly fragile — if you ever `brew uninstall rsync` or move to
a host without it, the script silently flips back to openrsync. Combine
with a `command -v rsync >/dev/null && rsync --version | grep -q '^rsync'`
sanity check at the top of the wrapper if you want it to fail fast in that
case.

## Verification

On the target machine:

```bash
/usr/bin/rsync --version | head -1
# If it says "openrsync: ..." you're on the openrsync constraint.
# If it says "rsync  version 3.x ... protocol version 31+" you're on GNU
# (Apple has started bundling GNU rsync on some newer macOS versions —
#  verify per-host).

# Then check what your LaunchAgent will actually use:
launchctl print gui/$UID/com.your.label | grep -A2 EnvironmentVariables
# Manually trace the PATH the launchd plist sets, and `which rsync` against
# it. The `command -v rsync` trick fails when run interactively because your
# shell PATH differs from launchd's — trace the literal PATH from the plist.
```

A LaunchAgent dry-run via `launchctl kickstart` will reveal binary-resolution
mismatches faster than waiting for the scheduled run.

## Example

Verified-failing-then-fixed wrapper from `bin/claude-session-backup`
(weekly sync to a remote host via Tailscale SSH):

```bash
# BEFORE — failed under launchd with "unrecognized option '--info=stats2'"
rsync -az --delete --info=stats2 \
    -e "ssh ${SSH_OPTS[*]}" \
    "$SRC" \
    "${REMOTE_HOST}:${REMOTE_BASE}/projects/"

# AFTER — openrsync-compatible
rsync -az --delete --stats \
    -e "ssh ${SSH_OPTS[*]}" \
    "$SRC" \
    "${REMOTE_HOST}:${REMOTE_BASE}/projects/" \
    2>&1 | tail -20
```

The `--stats` output is less rich than `--info=stats2` (one summary block
instead of per-type breakdowns) but covers the common need: total bytes
sent/received, speedup, elapsed time.

## Notes

- **`openrsync`'s "2.6.9 compatible" claim is conservative.** It actually
  supports a few protocol-3.x features (compression, ACLs, xattrs) but
  rejects new CLI flags introduced after 2.6.9. Trust the flag-reject
  behavior, not the version string, when deciding what's safe.
- **The PATH gotcha is the actual production trap.** Most authors test
  their script interactively, where `/opt/homebrew/bin` is on PATH. The
  failure surfaces only later under launchd / cron / SSH-from-elsewhere.
  Always test scripts in the exact PATH context they'll run under
  production.
- **Stripping rsync output with `| tail -N` can mask the failure.**
  `pipefail` should propagate the rsync exit code, but option ordering and
  shell version matters. Run the rsync without piping first when debugging
  a wrapper.
- **Newer macOS may eventually ship GNU rsync.** Apple has been migrating
  bundled tools to GNU/LGPL alternatives in some versions. Don't hardcode
  the assumption — gate behavior on `rsync --version` if it matters.
- Related: [[homebrew-cellar-versioned-path-breakage]] (don't hardcode
  Cellar paths if you go the Homebrew route; use `/opt/homebrew/opt/<formula>`
  symlinks);
  [[1password-cli-launchd-hang]] (other macOS LaunchAgent SSH/CLI traps);
  [[launchd-log-rotation-fd-rebind]] (LaunchAgent log handling, same
  problem-space).

## References

- Apple openrsync man page (BSD): https://man.openbsd.org/openrsync
- GNU rsync changelog (`--info` was added in 3.1.0):
  https://download.samba.org/pub/rsync/NEWS
- Apple's bundled-rsync swap (background): https://github.com/openbsd/src/tree/master/usr.bin/rsync
- Homebrew rsync formula: https://formulae.brew.sh/formula/rsync
