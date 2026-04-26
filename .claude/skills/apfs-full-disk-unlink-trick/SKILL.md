---
name: apfs-full-disk-unlink-trick
description: |
  Free space on a 100%-full APFS volume when `: > file`, `truncate -s 0`, and `rm`
  all fail with ENOSPC. Use when: (1) macOS `df -h` shows 100% full and
  `Avail=100Mi` on a multi-TB disk, (2) truncating a huge log file fails with
  "No space left on device" even though you're trying to SHRINK it, (3)
  `/usr/bin/truncate -s 0 <file>` returns ENOSPC, (4) `cp /dev/null <file>`
  also fails. The fix is `/bin/unlink <file>` — a pure directory-entry removal
  with no block allocation. `rm` may also fail because it tries to stat/check
  perms first; `unlink` is the narrowest syscall path.
author: Claude Code
version: 1.0.0
date: 2026-04-18
---

# APFS Full-Disk Recovery: `unlink` vs `truncate`

## Problem

On a fully-exhausted APFS volume (100% used, Avail in MB on a multi-TB disk),
**every write path that needs even one new metadata block fails with ENOSPC**,
including truncation. This is counterintuitive — users reach for `truncate -s 0`
or `: > file` expecting them to *shrink* the file, not realizing both paths go
through `open(O_TRUNC)` which on APFS must allocate a new inode version
(copy-on-write metadata). With zero blocks free, the open fails.

## Context / Trigger Conditions

- `df -h /` shows 100% capacity, Avail in low MB on a multi-TB drive
- A runaway log file (often a daemon in an exception loop) ate the space
- `: > /path/to/big.log` → `zsh: no space left on device: /path/to/big.log`
- `/usr/bin/truncate -s 0 /path/to/big.log` → `No space left on device`
- `cp /dev/null /path/to/big.log` → same error
- `rm` may also fail (stat + unlink + fsync of parent dir) depending on filesystem state
- Downstream: other processes fail to write `.tmp` files for atomic renames, cascading into app-level errors that *look* like auth/API/quota problems

## Solution

Use `/bin/unlink <file>` — the thinnest possible syscall path to remove a
directory entry. It does not allocate, does not truncate, does not stat the
content. Space is reclaimed immediately once no open FDs reference the inode.

```bash
/bin/unlink /path/to/offending-file.log
```

**Then force-reclaim held inodes:** any process still holding the file open
(common with daemons) will keep the blocks allocated until it closes the FD.
Stop or kickstart the responsible service so the old inode is released:

```bash
launchctl kickstart -k gui/$(id -u)/com.example.offending-daemon
# or for a non-launchd process:
kill <pid>  # and let it restart, or reopen its log
```

Verify with `df -h /` immediately after unlink (before and after a process
restart, to see whether the FD was the bottleneck).

## Verification

```bash
df -h / | tail -1      # Before: 100% / 100Mi avail
/bin/unlink /path/to/offending-file.log
df -h / | tail -1      # After:  should show freed space
```

If `df` still shows full after unlink, a process is holding the deleted
inode open:

```bash
lsof +L1 2>/dev/null | awk '$NF==0 && /deleted/'  # find open-deleted files
```

## Example

**2026-04-18 incident**: `~/.openclaw/logs/dog-walk-listener.log` grew to
703GB (sparse) after a `firebase_messaging` tight exception loop. Disk 100%.

```bash
# Symptom
$ df -h /
/dev/disk3s1s1   1.8Ti    12Gi   101Mi   100%   /

# First attempts — all fail
$ : > ~/.openclaw/logs/dog-walk-listener.log
zsh: no space left on device: ...
$ /usr/bin/truncate -s 0 ~/.openclaw/logs/dog-walk-listener.log
truncate: ...: No space left on device

# Fix
$ /bin/unlink ~/.openclaw/logs/dog-walk-listener.log
$ df -h /
/dev/disk3s1s1   1.8Ti    12Gi   703Gi   2%   /
```

703GB reclaimed instantly. The daemon was then kickstarted to drop the
zombie FD (though in this case the `unlink` alone was enough because the
stdout FD went to a launchd-managed path that was reopened on next write).

## Notes

- The same principle applies on ext4 and other COW-ish filesystems, but APFS
  is especially prone because of its aggressive copy-on-write metadata.
- Before deleting, verify the file is garbage: `tail -30 <file>` to confirm
  runaway duplicate output, not a legitimate large log.
- If you need the file preserved (e.g., to analyze later), this trick won't
  help — you need to first free space elsewhere (purge snapshots with
  `tmutil deletelocalsnapshots /`, empty Trash, delete Time Machine local
  backups) and *then* rotate normally.
- Diagnosis ordering tip: when a multi-service system shows auth/API/quota
  errors on the surface, check `df -h` before spelunking error logs. ENOSPC
  cascades through write-atomic `.tmp` rename patterns and surfaces as
  misleading higher-level errors (failed API calls, stale session writes,
  WebSocket handshake parse errors).

## References

- APFS copy-on-write metadata: Apple Platform Security Guide (search
  "APFS Copy-on-Write")
- `unlink(2)` vs `truncate(2)` vs `open(O_TRUNC)` — the syscalls are
  documented in `man 2 unlink`, `man 2 truncate`, `man 2 open`
