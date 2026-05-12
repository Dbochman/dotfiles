---
name: tailscale-taildrop-receiver-jam
description: |
  Diagnose Tailscale Taildrop (`tailscale file cp` / `tailscale file get`) pipelines
  that silently jam after working for a while. Use when: (1) inter-node state sync via
  `tailscale file cp` was working then stopped delivering, (2) the pusher's stderr
  shows "500 Internal Server Error: too many retries trying to rename
  ...stdin.txt.<random>.partial" to "stdin.txt", (3) the receiver logs show
  "refusing to overwrite file: open .../stdin (N).txt: file exists", (4) downstream
  consumers (vacancy automation, dashboards, evaluation jobs) are reading state files
  whose mtime is days/weeks old but the LaunchAgent firing the pull-side script shows
  no errors and is exiting 0, (5) a script using `tailscale file get --wait` only
  processes one file per invocation and breaks out of its loop. Also covers the
  Tailscale 1.56+ behavior change where stdin pushes (`echo X | tailscale file cp -
  host:`) are renamed from `stdin` to `stdin.txt` on arrival, breaking legacy
  `if [ -f stdin ]` consumers.
author: Claude Code
version: 1.0.0
date: 2026-05-10
---

# Tailscale Taildrop Receiver-Jam

## Problem

Tailscale's Taildrop file transfer (`tailscale file cp` from sender,
`tailscale file get` on receiver) refuses to overwrite existing files in
the destination directory. If the consumer of `tailscale file get` doesn't
drain *every* file from `RECV_DIR` on each invocation, stragglers accumulate
and eventually collide with new arrival names. From that point forward,
the pusher gets a 500 error from the Taildrop service ("too many retries
trying to rename"), the receiver's `tailscale file get` returns success
without delivering, and the whole pipeline silently delivers stale data
to downstream consumers.

The Tailscale 1.56+ rename of stdin pushes from `stdin` → `stdin.txt`
compounds this: legacy receiver scripts with `if [ -f stdin ]` checks
become dead code, and their fallback `for f in $RECV_DIR/*; do ...; break; done`
patterns leave N-1 stragglers per fire.

## Context / Trigger Conditions

- Pusher (`tailscale file cp ... peer:`) stderr contains:
  `500 Internal Server Error: too many retries trying to rename "/Library/Tailscale/files/<user>/stdin.txt.<random>.partial" to "stdin.txt"`
- Receiver log contains repeated:
  `refusing to overwrite file: open <RECV_DIR>/stdin (N).txt: file exists`
- Receiver's incoming dir has `stdin.txt`, `stdin (1).txt`, ... `stdin (9).txt`
  all left over from prior invocations
- Downstream state file (e.g. `crosstown-scan.json`) mtime is days/weeks
  stale even though the pusher's LaunchAgent is firing on schedule
- The receiver script processes exactly one file per invocation and `break`s

## Solution

Rewrite the receiver to drain the entire `RECV_DIR` after `tailscale file get --wait`
returns, picking the newest file as canonical and unconditionally deleting every
straggler:

```bash
log "Waiting for Tailscale file transfer..."
tailscale file get --wait "$RECV_DIR/" 2>/dev/null

shopt -s nullglob
files=("${RECV_DIR}"/*)
shopt -u nullglob

if [ "${#files[@]}" -eq 0 ]; then
  log "WARN: tailscale file get returned but RECV_DIR is empty"
  exit 0
fi

# Newest by mtime wins the canonical slot
newest=""
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
    newest="$f"
  fi
done

mv "$newest" "${STATE_DIR}/<canonical-name>.json"
log "Received via Tailscale (from $(basename "$newest"); ${#files[@]} file(s) in queue)"

# Drop every straggler so the next push doesn't hit name collisions
for f in "${files[@]}"; do
  [ -f "$f" ] && rm -f "$f"
done
```

Drop the legacy `if [ -f stdin ]` check entirely — Tailscale 1.56+ won't
produce that filename. The unified loop handles `stdin.txt`, `stdin (N).txt`,
and any other arrival name.

After deploying, manually drain the existing backlog:
```bash
rm -v <RECV_DIR>/stdin*.txt   # or whatever pattern exists
```

## Verification

1. Confirm the receiver dir is empty: `ls <RECV_DIR>/`
2. Trigger a fresh push from the peer:
   `ssh <peer> 'launchctl kickstart -k gui/$(id -u)/<pusher-label>'`
3. Wait ~30s, then verify:
   - Canonical state file mtime is current (`ls -la <state-file>`)
   - Receiver dir is still empty (no stragglers)
   - Receiver log shows "Received via Tailscale (from stdin.txt; 1 file(s) in queue)"
4. Trigger 2-3 more pushes back-to-back to confirm no jam recurs.

## Example

Symptom (from this session, 2026-05-10):
```
$ tail -3 ~/.openclaw/logs/presence-detect.log    # MBP pusher side
[2026-05-10 10:58:41] WARN: Failed to push crosstown state to Mac Mini:
2026/05/10 10:58:41 500 Internal Server Error: too many retries trying
to rename "/Library/Tailscale/files/dylanbochman-gmail.com-uid-7876609327624922/
stdin.txt.nkveM75j2S11CNTRL.partial" to "stdin.txt"

$ tail -5 ~/.openclaw/logs/presence-receive.log    # Mini receiver side
refusing to overwrite file: open .../incoming/stdin (8).txt: file exists
refusing to overwrite file: open .../incoming/stdin (9).txt: file exists
refusing to overwrite file: open .../incoming/stdin.txt: file exists

$ ls -la .../crosstown-scan.json    # canonical state, was stale 9 days
-rw-r--r--@ 1 dbochman staff 306 May  1 11:17 crosstown-scan.json
```

After rewriting the receiver per Solution, the next MBP push completed in
<30s and `crosstown-scan.json` mtime was current.

## Notes

- The Tailscale 1.56+ behavior change adding `.txt` to stdin-piped pushes
  is documented in their release notes but easy to miss. Audit any
  `if [ -f stdin ]` or `mv stdin X` patterns in receiver scripts.
- Don't try to be clever about deduplication — if multiple files arrived
  while the receiver was offline, take newest-by-mtime and drop the rest.
  Older snapshots are stale anyway.
- Watch for downstream silent staleness: this kind of jam doesn't trigger
  a LaunchAgent failure (the receiver script can still exit 0 by
  processing one file), so you'll only notice when a downstream consumer
  (vacancy actions, evaluation jobs, dashboards) acts on data that's
  obviously old. Add a freshness assertion (e.g., reject state >30 min old)
  in the consumer to surface this earlier.
- The same pattern applies to any directory-based message queue where the
  producer can't overwrite — not just Tailscale Taildrop. SCP with
  conflict-on-rename, S3 PUT-if-not-exists, etc., have the same shape.

## References

- Tailscale Taildrop docs: https://tailscale.com/kb/1106/taildrop
- Tailscale 1.56 release notes (where `--wait` was added to `file get`):
  https://tailscale.com/changelog
