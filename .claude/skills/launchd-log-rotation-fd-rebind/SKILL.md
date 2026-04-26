---
name: launchd-log-rotation-fd-rebind
description: |
  Rotate a LaunchAgent log file from within its own wrapper script without
  losing post-rotation log output. Use when: (1) writing a wrapper script that
  implements size-based log rotation for a service whose plist sets
  StandardOutPath/StandardErrorPath, (2) after `mv $LOG $LOG.1` the rotated
  file keeps growing instead of the new $LOG, (3) child processes inherit
  FDs that still point at the renamed file, (4) building self-healing
  services that must survive runaway log scenarios. The gotcha: launchd
  opens StandardOutPath once at spawn and passes FD 1/2 to the wrapper.
  After rename, the wrapper MUST `exec 1>>"$LOG" 2>&1` to rebind inherited
  FDs to the new inode before exec'ing the child.
author: Claude Code
version: 1.0.0
date: 2026-04-18
---

# LaunchAgent log rotation: FD rebind after rename

## Problem

You want your LaunchAgent's wrapper script to rotate its own log file when
it grows too large (classic use case: a protective measure after a runaway
logging incident filled the disk). Naive rotation:

```bash
mv "$LOG" "$LOG.1"
: > "$LOG"
exec /path/to/real/program
```

…appears to work — the new empty `$LOG` exists on disk — but every byte the
real program writes still lands in `$LOG.1`. The "new" log stays empty
forever. Meanwhile `$LOG.1` keeps growing past the rotation threshold and
your mitigation is ineffective.

## Context / Trigger Conditions

- A LaunchAgent plist sets `<key>StandardOutPath</key>` (and/or `StandardErrorPath`)
- Your wrapper script implements size-based rotation before `exec`'ing the real binary
- After rotation fires, `$LOG` stays at 0 bytes while `$LOG.1` continues to grow
- `lsof -p <pid>` on the running service shows FD 1 and 2 pointing at `$LOG.1 (deleted or renamed)`
- The service itself doesn't support SIGHUP-on-logrotate

## Solution

Rebind FD 1 and FD 2 in the wrapper to the new inode AFTER the rename, BEFORE
exec'ing the child. The wrapper itself inherits the FDs launchd opened at
spawn, and those FDs are passed through to the child via exec. Redirecting
in the wrapper reopens them against the new inode:

```bash
#!/bin/bash
set -euo pipefail

LOG="$HOME/Library/Logs/myservice.log"
MAX_BYTES=$((100 * 1024 * 1024))
KEEP=3

if [[ -f "$LOG" ]]; then
  SIZE=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [[ $SIZE -gt $MAX_BYTES ]]; then
    # Shift older rotations out of the way
    i=$KEEP
    while [[ $i -gt 1 ]]; do
      prev=$((i - 1))
      [[ -f "$LOG.$prev" ]] && mv -f "$LOG.$prev" "$LOG.$i"
      i=$prev
    done
    mv -f "$LOG" "$LOG.1"
    : > "$LOG"

    # CRITICAL: rebind our inherited FDs to the new inode. Without this, any
    # writes by us or our children still go to the renamed file (the old
    # inode launchd handed us at spawn time).
    exec 1>>"$LOG" 2>&1

    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) rotated log at ${SIZE} bytes"
  fi
fi

exec /path/to/real/program
```

The `exec 1>>"$LOG" 2>&1` is a redirection that doesn't spawn anything; it
just reopens the current shell's FD 1 (and aliases FD 2 to the same) against
`$LOG`. When we `exec /path/to/real/program` afterward, the program inherits
those freshly-opened FDs.

## Verification

After rotation fires:

```bash
# Check the new log is growing, not the rotated one
ls -lh $LOG $LOG.1
# Expected: $LOG has recent mtime and growing size; $LOG.1 is frozen

# Inspect the actual FDs the running child has open
lsof -p $(pgrep -f myservice) | awk '$4 ~ /^[12]/'
# Both FD 1 and FD 2 should show $LOG (not $LOG.1)
```

## Example

Pattern used in the dog-walk-listener fix (2026-04-18 OpenClaw incident):

```bash
# openclaw/skills/dog-walk/dog-walk-listener-wrapper.sh
LOG="$HOME/.openclaw/logs/dog-walk-listener.log"
MAX_BYTES=$((100 * 1024 * 1024))  # 100MB
KEEP=3

if [[ -f "$LOG" ]]; then
  SIZE=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [[ $SIZE -gt $MAX_BYTES ]]; then
    i=$KEEP
    while [[ $i -gt 1 ]]; do
      prev=$((i - 1))
      [[ -f "$LOG.$prev" ]] && mv -f "$LOG.$prev" "$LOG.$i"
      i=$prev
    done
    mv -f "$LOG" "$LOG.1"
    : > "$LOG"
    exec 1>>"$LOG" 2>&1    # <-- the critical line
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrapper: rotated log at ${SIZE} bytes (kept $KEEP)"
  fi
fi

exec "$PYTHON" "$LISTENER"
```

## Notes

- **This rotates on (re)start only.** For a service that runs for weeks without
  restart, pair it with an in-process self-exit trigger (e.g., when the child
  detects runaway output it can `os._exit(1)` to trigger KeepAlive restart,
  which cycles through the wrapper and rotates). See the
  `python-stderr-dedup-via-dup2` skill for an example kill-switch.
- **Don't use logrotate-style `copytruncate`.** It requires extra disk space
  at the worst possible moment (when you're rotating because the log is huge).
  Rename-plus-new-inode uses zero extra space.
- **`logger(1)` / newsyslog alternative.** macOS ships `newsyslog` which can
  rotate on schedule or size, but it requires a config in `/etc/newsyslog.d/`
  (root) or `/usr/local/etc/newsyslog.conf`. For a LaunchAgent running as the
  user with no sudo, the in-wrapper pattern is simpler.
- **Watch out for `set -e` interactions.** The `stat -f%z` form works only on
  BSD (macOS); on Linux use `stat -c%s`. `|| echo 0` shields `set -e`.
- **Why not `cat /dev/null > $LOG`?** It doesn't help — `>` truncates the
  file through FD 1 redirection on the *current* inode the shell has open,
  which is fine until you try to `mv` first. After `mv`, any redirection to
  `$LOG` will fail on a full disk (see the `apfs-full-disk-unlink-trick`
  skill for why).

## References

- `launchd.plist(5)` — `man 5 launchd.plist`, see `StandardOutPath` and
  `StandardErrorPath` semantics.
- Bash redirection: `help exec` (`exec [redirection]` without a command is
  the "reopen FDs for this shell" form).
