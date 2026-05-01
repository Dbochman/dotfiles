---
name: self-deploying-script-skips-self
description: |
  Diagnose and fix self-deploying shell scripts (run by launchd/cron/systemd) that
  appear to run cleanly every day but silently strand their own recent changes
  because the deploy loop excludes the script's own file extension. Use when:
  (1) a launchd/cron job logs successful runs daily, but a recently-committed
  fix to its own logic (or to a new deploy block it added) never takes effect,
  (2) the deployed copy of a deploy script differs in size/line-count from the
  repo HEAD copy of the same script, (3) features added to a deploy script in
  one commit work in subsequent commits' targets but not in older targets that
  the same commit also touched, (4) `git log` shows the script was updated
  weeks/months ago but the running copy still behaves like the old version,
  (5) wrapper-deploy loops with case-statement filters like
  `*.command|*.sh|*.py|*.md` that were intended to skip non-wrappers but also
  skip the deploy script itself. The fix is a self-update block at the very
  end of the script using cp-then-mv (rename is atomic; plain cp would
  truncate the running file mid-execution and corrupt bash). Applies to
  dotfiles-pull patterns, ansible-pull, any self-bootstrapping deploy job.
author: Claude Code
version: 1.0.0
date: 2026-05-01
---

# Self-Deploying Script Skips Itself

## Problem

A shell script run by launchd / cron / systemd does daily housekeeping —
git-pulls a tracked repo and copies files (skills, wrappers, configs) from the
repo into runtime locations. The script's own logic also lives in the repo,
and over time its deploy logic gets extended (new deploy blocks, fixes to
existing ones).

But the script never deploys **itself**. The wrapper-deploy loop has a case
statement that skips files by extension to avoid copying non-executable
helpers:

```bash
for wrapper in "$BIN_SRC"/*; do
  [ -f "$wrapper" ] || continue
  fname=$(basename "$wrapper")
  case "$fname" in
    *.py|*.sh|*.command|*.md|*.json|*.yaml) continue ;;
  esac
  cp "$wrapper" "$BIN_DST/$fname"
done
```

The intent was "skip Python/shell/markdown files." The collateral damage was
"skip the deploy script itself" (`dotfiles-pull.command`). Months of fixes
to the script silently stranded — the launchd plist runs the **deployed**
copy at `~/.openclaw/bin/dotfiles-pull.command`, not the repo copy at
`~/dotfiles/openclaw/bin/dotfiles-pull.command`. Daily runs succeed
(git pull works, skills deploy, log lines look healthy), but any deploy
block added to the script after the initial bootstrap simply never executes.

## Context / Trigger Conditions

- **Symptom A**: a file the deploy script *should* be syncing is stale on the
  target host. Logs show clean daily runs. The repo copy of the deploy
  script on the host is current. But the file the deploy script targets is
  weeks/months out of date.
- **Symptom B**: `git log` shows the deploy script was updated to add a new
  deploy block (e.g., `openclaw/workspace/scripts/`), but searching the
  daily log file for the new block's log line returns zero matches —
  including for the day the change was committed.
- **Symptom C**: a downstream regression hits production days/weeks after
  the fix was committed. "We pushed the fix — why is it still broken?"
- **Symptom D**: launchd plist `ProgramArguments` points at a deployed copy
  (e.g., `/Users/me/.openclaw/bin/foo.command`) rather than at the repo
  (`/Users/me/dotfiles/openclaw/bin/foo.command`), AND the deploy logic in
  the script doesn't have an explicit self-deploy block.
- **Symptom E**: deploy loop has a `case` filter that excludes the script's
  own extension. Common filters: `*.command|*.sh|*.py`.

## Diagnostic

One command confirms the diagnosis:

```bash
wc -l <deployed-path> <repo-path>
# e.g.:
wc -l ~/.openclaw/bin/dotfiles-pull.command ~/dotfiles/openclaw/bin/dotfiles-pull.command
```

If line counts differ, the deployed copy is stale and the script is not
self-deploying. Confirming further:

```bash
diff <deployed-path> <repo-path> | head -50
```

Will show which deploy blocks are missing from the running copy.

You can also inspect the deploy log for log lines that the new deploy block
emits — absence is signal:

```bash
grep "workspace.*scripts" ~/.openclaw/logs/dotfiles-pull.log
# zero matches → that block has never run, even if commit is months old
```

## Solution

Add a self-update block as the **very last** operation in the script,
just before any cleanup/exit. Use cp-then-mv for atomic replacement:

```bash
# Self-update: keep the deployed copy of this script in sync with repo HEAD.
# Without this, fixes to this script (and any new deploy blocks it adds)
# never reach the host — the wrapper-deploy loop above skips *.command,
# and launchd runs the DEPLOYED copy. Use cp+mv for atomic replace so the
# still-running bash process keeps reading the old inode to EOF.
SELF_SRC="$REPO/path/in/repo/this-script.command"
SELF_DST="$HOME/.local/bin/this-script.command"
if [ -f "$SELF_SRC" ] && ! cmp -s "$SELF_SRC" "$SELF_DST"; then
  cp "$SELF_SRC" "$SELF_DST.new"
  chmod +x "$SELF_DST.new"
  mv "$SELF_DST.new" "$SELF_DST"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) self: updated $SELF_DST from repo" >> "$LOG"
fi
```

Why this specific shape:

1. **`cp` to a `.new` sibling, then `mv`**: `mv` on the same filesystem
   is `rename(2)`, an atomic directory-entry swap. The new file gets a
   new inode; the old inode persists until all open FDs close. Bash's
   open file descriptor on the running script still points at the old
   inode and continues reading it cleanly to EOF. The next launchd run
   opens the new inode and gets the new code.

2. **Why NOT plain `cp` over the destination**: `cp` calls
   `open(target, O_WRONLY|O_TRUNC)` — it truncates the existing file
   in place and rewrites it. Bash's open FD now reads from a file
   that's been truncated to length 0 then partially rewritten; if
   bash's internal read offset is past the new EOF, it gets premature
   end-of-file and stops mid-script. Symptoms vary: silent early exit,
   syntax errors mid-statement, missing trailing log lines.

3. **Position at end of script**: by the time bash reaches the
   self-update block, it has already read past every other line.
   Replacing the file now can't affect the current run even if mv
   weren't atomic.

4. **`cmp -s` short-circuit**: avoids unnecessary writes (and a spurious
   log line) when the files already match. Will be the steady-state on
   most days. Only logs on actual updates, which is when you want a
   trail.

5. **`chmod +x` on the new file before mv**: ensures the executable bit
   is set before the rename swaps it into place, so there's no window
   where the deployed path exists but isn't executable.

## Bootstrap

The first deployment of the new self-update block has to be applied
manually — the running script doesn't have the block yet, so it can't
deploy itself. One scp / cp by hand is the bootstrap; from then on,
self-update keeps it current.

## Verification

1. After bootstrapping the new script, trigger a manual run:
   `bash <deployed-path>` (or however it's normally invoked).
2. Check the log for the new `self:` line on first run when the bootstrap
   was older than the repo HEAD: `tail ~/.openclaw/logs/dotfiles-pull.log`.
3. Compare line counts again: `wc -l <deployed-path> <repo-path>` —
   should match.
4. Make a trivial change to the script (e.g., a comment), commit + push,
   trigger a run, confirm the deployed copy now has the comment.
5. On steady state, the `self:` log line should NOT appear (cmp short-
   circuit) — only when the file actually changed.

## Notes

- **Same filesystem requirement**: `mv` is only atomic via `rename(2)` if
  source and destination are on the same filesystem. If the repo is on
  one volume and the deploy target is on another, `mv` falls back to
  cp+unlink and you lose atomicity. Both should be on the same disk for
  this pattern to be safe — almost always true for `~/repo/...` →
  `~/.local/bin/...`.

- **Doesn't apply to interpreted-by-line scripts that don't fully buffer**:
  bash typically reads its input in chunks but does NOT load the entire
  script upfront. Replacing the running file mid-execution is a real
  hazard, which is why the atomic-rename pattern matters. If you're
  ever tempted to put the self-update earlier in the script, don't —
  the failure mode is silent corruption, not a clear error.

- **Process supervisors that re-exec on file change** (not common in
  launchd/cron, but possible in some systemd setups with `ExecReload`)
  could see the new inode and restart. Usually a non-issue for periodic
  jobs that are invoked fresh each tick.

- **Related symptom — the launchd plist ALSO needs to be deployable**:
  if the script's plist itself changes (new env vars, schedule changes),
  the deployed plist at `~/Library/LaunchAgents/` is a separate
  deployment problem. `launchctl load -F` on the new plist is the
  bootstrap; can be added to the same self-update block but requires
  more care (loading a plist while the agent is running can confuse
  launchd — usually unload + load).

- **Log rotation interaction**: if the deploy script also rotates its
  own log, do that BEFORE the self-update block so the rotation logic
  is the version that just ran (not the new version that may have
  different rotation rules). The new version takes effect next tick.

## Example

The trigger session:

```
User: "Julia is at the cabin but presence shows her at crosstown."
```

Investigation chain:
1. `~/.openclaw/presence/cabin-scan.json` had `{"error":"parse_failed"}`.
2. `presence-detect.sh` had `NODE="/opt/homebrew/bin/node"` but Mini's
   node is keg-only at `/opt/homebrew/opt/node@22/bin/node` →
   `parse_failed` from the `||` fallback. (This is a separate bug,
   covered by `homebrew-cellar-versioned-path-breakage` skill.)
3. Fixed the NODE path locally, committed, expected dotfiles-pull to
   carry it. But noticed the workspace-scripts deploy block in the
   script's repo HEAD had **never** logged a deploy. Block was added
   in commit `7a136ff` (months old).
4. `wc -l ~/.openclaw/bin/dotfiles-pull.command` = 102 lines.
   `wc -l ~/dotfiles/openclaw/bin/dotfiles-pull.command` = 228 lines.
   The deployed copy was 102 lines for months while the repo grew to
   228. Self-update block missing.
5. Fix: added self-update block at end. Bootstrap-deployed manually.
   Next run logged `workspace: deployed 10 scripts to ...` for the
   first time ever. Subsequent commits to either the script OR
   `openclaw/workspace/scripts/*` now reach the host on the next 6 AM
   pull without manual intervention.

## References

- `rename(2)` — POSIX guarantees atomicity on same filesystem.
- `open(2)` with `O_TRUNC` — explains why plain `cp` is unsafe for
  replacing a running script.
