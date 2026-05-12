---
name: err-trap-log-without-exit
description: |
  Diagnose deploy/sync scripts that "fail loudly" in the log (FATAL
  entries appear) but silently continue past the failure and produce
  bad outputs — typically re-deploying stale local files after a
  failed git pull, or running downstream steps on missing fetched
  data. Use when: (1) a script's log file contains entries like "FATAL: <X>
  failed at line N" but the script clearly kept running past that
  point and exited 0, (2) a daily deploy job consistently writes "all
  steps OK" output even though an upstream fetch step's stderr shows
  errors, (3) recently-pushed upstream changes never appear on the
  target host even though the deploy log shows "deployed N files",
  (4) a wrapper uses `set -e` + an ERR trap that only echoes/logs and
  doesn't `exit`, OR uses `set +e` around a "benign-failures" block
  with the trap still armed (set +e does NOT disable the trap), (5)
  the same script appears to "succeed" via exit code while important
  middle steps obviously failed. The fix is two-part: the ERR trap
  must `exit 1` (not just log), and the section-disabling-errexit
  must also detach the trap (`trap - ERR`) for the duration of the
  intentionally-non-strict block.
author: Claude Code
version: 1.0.0
date: 2026-05-12
---

# ERR trap that logs but doesn't exit — silent deploy continuation

## Problem

A common shell-script-template anti-pattern:

```bash
set -euo pipefail
trap 'echo "$(date) FATAL: failed at line $LINENO" >> "$LOG"' ERR

# ... main work ...

# A section where some commands are expected to occasionally fail (e.g.
# git stash pop with no stash, rm of an optional file). Author wants to
# tolerate those without aborting the whole script:
set +e
some_command_that_might_fail
ANOTHER_OUT=$(another_might_fail 2>&1)
ANOTHER_STATUS=$?
set -e

# Critical downstream work:
deploy_files
notify_users
```

This has **two interacting bugs**:

1. **The ERR trap only logs, doesn't exit.** When any command fails
   under `set -e`, the trap fires and writes "FATAL: failed at line N"
   to the log — but doesn't actually halt. The script keeps running
   the next statement. (Compare: a trap that ends with `exit 1` would
   actually halt.) Operators glance at the log, see FATAL entries,
   assume the script bailed — but it didn't. Critical downstream
   work ran with broken state.

2. **`set +e` doesn't disable the ERR trap.** This is the part that
   surprises most shell authors. `set +e` only disables errexit's
   automatic exit on non-zero — the trap itself is still armed and
   fires on every failed command inside the supposedly-"soft" block.
   Combined with bug #1, you get a flurry of FATAL log entries from
   commands that the author explicitly meant to tolerate, training
   the operator to ignore FATAL entries as noise.

3. **Downstream continues regardless.** Even if the ERR trap properly
   exited, `set +e` would suppress that exit too. So the script
   reaches its critical downstream steps having captured a non-zero
   status into a variable that nobody checked. The script logs "all
   N files deployed" using stale local state, hiding that the upstream
   fetch failed.

This bit a daily `dotfiles-pull` LaunchAgent: a pull-side merge
conflict left the repo in `UU` state, the next morning's pull failed
ff-only, the ERR trap logged FATAL twice, and the script then
re-deployed every file from the unchanged local checkout — silently
reverting commits that had been pushed upstream. Symptom on the
receiving end: a fix that landed in `~/dotfiles/` overnight was gone
the next morning, with no obvious cause.

## Context / Trigger Conditions

- A bash script uses `set -euo pipefail` AND `trap '...' ERR`
- The trap body echoes/logs but lacks an explicit `exit`
- One or more sections use `set +e ... set -e` to tolerate expected
  failures
- The script's log file contains "FATAL: ... line N" entries followed
  by normal-looking success log lines from later steps
- A deploy/sync step is involved: the script's job is to fetch
  something fresh and copy it somewhere — exactly the place where
  silent fallback to stale state is dangerous
- `git pull` / `rsync` / `curl` upstream-fetch failures are the most
  common upstream-fetch step that fails silently here

## Solution

### Fix the trap

Make the trap actually halt:

```bash
trap 'echo "$(date) FATAL: failed at line $LINENO" >> "$LOG"; exit 1' ERR
```

Or use a function for readability:

```bash
on_err() {
  local lineno=$1
  echo "$(date) FATAL: failed at line $lineno" >> "$LOG"
  exit 1
}
trap 'on_err $LINENO' ERR
```

### Detach the trap during intentionally-tolerant blocks

`set +e` only flips errexit — it does NOT silence the trap. If you
have a block where multiple expected-occasionally-failing commands
need to run without each one logging FATAL, **also** detach the trap:

```bash
trap - ERR        # detach: failures in this block stay quiet
set +e
some_command_that_might_fail
OUT=$(another_command 2>&1)
STATUS=$?
set -e
trap 'on_err $LINENO' ERR   # re-attach for the rest of the script
```

### Add an explicit success gate after the tolerant block

The captured `STATUS` from the tolerant block is the only signal that
the critical upstream step succeeded. Check it before letting the
downstream steps run:

```bash
if [ "$STATUS" -ne 0 ]; then
  echo "$(date) ABORT: upstream step failed (exit=$STATUS); refusing to run downstream" >> "$LOG"
  exit 1
fi

# ... downstream deploy/notify steps run only when STATUS=0 ...
```

The exit-1 here is what makes the failure visible to launchd / cron /
operator. Without it, deploy continues with stale upstream data.

## Verification

Three tests against your patched script:

1. **Clean run**: ordinary inputs → exit 0, downstream runs, log
   shows no ABORT/FATAL entries.

2. **Upstream-fetch failure**: deliberately break the upstream
   (e.g., `git remote set-url origin <nonexistent>`, then run) →
   exit 1, log shows ABORT, downstream does NOT run.

3. **Soft-failure inside tolerant block**: trigger a command that's
   *supposed* to occasionally fail (e.g., `git stash pop` with no
   stash) → log does NOT contain FATAL for that command, downstream
   still runs cleanly.

If test 2 produces "everything deployed" log lines, your fix is
incomplete — the success gate isn't in place.

## Example

Before (from dotfiles-pull.command):

```bash
set -euo pipefail
trap 'echo "$(date) FATAL: failed at line $LINENO" >> "$LOG"' ERR

cd "$REPO" || exit 1
DIRTY=$(git status --porcelain)

set +e
if [ -n "$DIRTY" ]; then
  git stash push -m "auto-stash" >/dev/null 2>&1
  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  git stash pop 2>/dev/null   # ← fires ERR trap if nothing to pop
fi
set -e

# 20+ lines of deploy logic — run regardless of $PULL_STATUS
cp -R skills/ ~/.openclaw/skills/
# ...
```

Operator-visible log after a failure:

```
2026-05-12T10:00:05Z FATAL: dotfiles-pull failed at line 41
2026-05-12T10:00:05Z stash-pop exit=1 No stash entries found.
2026-05-12T10:00:05Z FATAL: dotfiles-pull failed at line 41
2026-05-12T10:00:08Z skills: deployed 56 skills to /Users/dbochman/.openclaw/skills
2026-05-12T10:00:09Z wrappers: deployed 17 to /Users/dbochman/.openclaw/bin
... [stale files being re-deployed despite the FATALs] ...
```

After (the three-part fix applied):

```bash
set -euo pipefail
trap 'echo "$(date) FATAL: failed at line $LINENO" >> "$LOG"' ERR

cd "$REPO" || exit 1
DIRTY=$(git status --porcelain)

trap - ERR        # detach during soft block
set +e
PULL_STATUS=99
if [ -n "$DIRTY" ]; then
  git stash push -m "auto-stash" >/dev/null 2>&1
  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  git stash pop 2>/dev/null
fi
set -e
trap 'echo "$(date) FATAL: failed at line $LINENO" >> "$LOG"' ERR   # re-attach

# Success gate
if [ "$PULL_STATUS" -ne 0 ]; then
  echo "$(date) ABORT: git pull failed (exit=$PULL_STATUS); refusing to deploy stale files" >> "$LOG"
  exit 1
fi

# Downstream — only runs when upstream actually succeeded
cp -R skills/ ~/.openclaw/skills/
```

Test 3 output (broken-remote pull):

```
$ bash dotfiles-pull.command; echo "exit=$?"
exit=1

$ tail -2 ~/.openclaw/logs/dotfiles-pull.log
2026-05-12T18:40:04Z exit=128 fatal: repository 'https://...' not found
2026-05-12T18:40:04Z ABORT: git pull failed (exit=128); refusing to deploy stale files
```

No deploy log lines after ABORT — exactly the behavior you want.

## Notes

- Empty-trap-body bash (`trap '' ERR`) is **not** the same as
  `trap - ERR`. The first IGNORES the signal (more aggressive,
  prevents errexit from triggering). The second REMOVES the handler.
  Both serve "I don't want this trap to fire here," but the semantics
  matter if you're chaining traps.
- `set +e` combined with `pipefail` interacts subtly: pipeline-internal
  command failures still set non-zero exit codes that the trap fires
  on (when armed). If you trap+log on every pipeline exit you'll get
  noise from things like `grep` returning 1 for no matches.
- A complementary check is `set -E` (errtrace) which causes shell
  functions and subshells to inherit the ERR trap. Without it, a
  failure inside a function may not trigger your top-level trap. If
  you're seeing "the trap fires for some failures but not others,"
  check whether `set -E` is set.
- The exit-1 in the ABORT path matters specifically because launchd /
  cron / your CI runner needs a non-zero exit to flag the run as
  failed. A script that logs ABORT and then `exit 0` looks healthy
  from the outside.

## References

- POSIX shell `trap` semantics:
  https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#trap
- Bash ERR trap and `set -E`:
  https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html
- BashFAQ #105 (set -e quirks):
  https://mywiki.wooledge.org/BashFAQ/105
