---
name: tailscale-taildrop-receiver-jam
description: >-
  Diagnose Tailscale Taildrop (`tailscale file cp` / `tailscale file get`) pipelines that silently jam
  after working for a while.
author: Claude Code
version: 1.1.0
date: 2026-06-21
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
- A receiver that drains only *after* `file get` can still jam: an existing
  collision may make `file get` fail before post-receive cleanup runs

## Solution

Use one queue-drain function both before and after `tailscale file get --wait`.
The pre-drain recovers a file left by a crash before Tailscale tries to create
another `stdin.txt`; the post-drain handles the new arrival. Pick the newest
file as canonical and unconditionally remove every straggler:

```bash
process_queue() {
  shopt -s nullglob
  local files=("${RECV_DIR}"/*)
  shopt -u nullglob
  [ "${#files[@]}" -gt 0 ] || return 1

  local newest="" f
  for f in "${files[@]}"; do
    [ -f "$f" ] || continue
    if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
      newest="$f"
    fi
  done
  [ -n "$newest" ] || return 1

  mv -f "$newest" "${STATE_DIR}/<canonical-name>.json"
  for f in "${files[@]}"; do
    [ -f "$f" ] && rm -f "$f"
  done
}

# Recover a prior arrival before asking Tailscale to write stdin.txt again.
process_queue || true

log "Waiting for Tailscale file transfer..."
if ! output=$(tailscale file get --wait "$RECV_DIR/" 2>&1); then
  log "ERROR: tailscale file get failed: $(printf '%s' "$output" | tr '\n' ' ' | cut -c1-300)"
  sleep 30
  exit 1
fi

process_queue || log "WARN: tailscale file get returned but queue is empty"
```

Drop the legacy `if [ -f stdin ]` check entirely — Tailscale 1.56+ won't
produce that filename. The unified loop handles `stdin.txt`, `stdin (N).txt`,
and any other arrival name.

Capture CLI output and add a retry delay. Sending raw stdout/stderr directly
to a plist-owned log can otherwise turn one collision into a tight KeepAlive
loop and tens of megabytes of duplicate lines. Prefer a script-owned bounded
log and `/dev/null` for plist stdout/stderr.

## Verification

1. Confirm startup pre-drained the receiver dir: `ls <RECV_DIR>/`
2. Trigger a fresh push from the peer:
   `ssh <peer> 'launchctl kickstart -k gui/$(id -u)/<pusher-label>'`
3. Wait ~30s, then verify:
   - Canonical state file mtime is current (`ls -la <state-file>`)
   - Receiver dir is still empty (no stragglers)
   - Receiver log shows "Received via Tailscale (from stdin.txt; 1 file(s) in queue)"
4. Trigger 2-3 more pushes back-to-back to confirm no jam recurs.
5. Confirm the raw plist log is not growing and the script-owned log contains
   one summarized receive record per transfer.

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
