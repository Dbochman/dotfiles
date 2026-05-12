---
name: bash-set-e-cmdsub-silent-abort
description: |
  Diagnose bash scripts using `set -euo pipefail` that silently abort
  mid-execution when a function called inside command substitution
  returns a non-zero exit. Use when: (1) a `set -euo pipefail` script
  logs the first half of its work but never reaches the second half,
  with no error and no log line indicating why, (2) a "guard function"
  pattern like `RUNNING_JOBS=$(cron_job_running); if [[ $? -eq 0 ]];
  then ...` never enters either branch, (3) a watchdog/cron script
  consistently logs "detected condition X" but never logs "took action
  Y" despite no visible error, (4) you're debugging why a `case` arm
  with multiple commands silently exits after the first command that
  calls a function via `$(fn)` whose normal return convention includes
  exit 1 for the "no" answer (e.g., "no jobs running", "no files
  found", "no match"). The fix is `RUNNING_JOBS=$(fn || true)` plus
  switching the branch check from `$?` to non-empty output, since the
  function's stdout is what you actually want anyway.
author: Claude Code
version: 1.0.0
date: 2026-05-11
---

# bash `set -e` + command substitution silently aborts

## Problem

A bash script with `set -euo pipefail` calls a helper function via
command substitution to decide branching, where the function's normal
return convention is `exit 0` for "yes" and `exit 1` for "no":

```bash
guard_fn() {
  if [[ <condition> ]]; then
    echo "<details>"
    return 0
  fi
  return 1   # <- normal "no" path
}

# ... later, inside a case arm or other multi-step block:
INFO=$(guard_fn)
if [[ $? -eq 0 ]]; then       # NEVER REACHED when guard_fn returns 1
  log "branch A: $INFO"
else
  log "branch B"
  do_the_thing
fi
```

Symptom: the script logs everything up to just *before* the `$(guard_fn)`
call, then stops. Neither branch runs. `do_the_thing` never executes.
No error message, no trace, no exit-code-1 from launchd telling you
something went wrong — the script ran to "completion" as far as the
caller is concerned.

This bit a BB watchdog cron job on the Mac Mini for ~3 weeks: it logged
"GATEWAY BB PLUGIN DEAD" detections daily but never executed the
`launchctl kickstart` restart that followed in the same `case` arm.
The gateway happened to be healthy the whole time (a separate detection
bug), so the silent abort caused no harm — but it would have hidden a
real failure.

## Context / Trigger Conditions

- Script starts with `set -euo pipefail` (or just `set -e`)
- A multi-step block (case arm, if branch, function body) uses
  `VAR=$(fn)` followed by `if [[ $? -eq 0 ]]` to branch
- `fn` follows the shell convention of `return 1` / `exit 1` as a
  normal "no" answer (e.g., guard functions, "is X running" checks,
  "are there any Y" probes)
- The script's log shows execution up to the `$(fn)` line then stops
- Calling `fn` standalone (outside `$()`) does not exit the script —
  but inside `$()` it does, on this bash version
- Running the same script with `bash -x` shows execution stopping
  immediately after the `VAR=$(fn)` assignment with no other output

## Solution

```bash
# WRONG: dies silently when fn returns 1, $? branch never reached
RUNNING_JOBS=$(guard_fn)
if [[ $? -eq 0 ]]; then
  log "branch A: $RUNNING_JOBS"
else
  log "branch B"
fi

# RIGHT: `|| true` neutralizes set -e for this line, and we branch on
# the function's stdout (which is what we actually care about — the
# function prints details when "yes", nothing when "no").
RUNNING_JOBS=$(guard_fn || true)
if [[ -n "$RUNNING_JOBS" ]]; then
  log "branch A: $RUNNING_JOBS"
else
  log "branch B"
fi
```

Alternative if you really need the exit code (rare):

```bash
set +e
RUNNING_JOBS=$(guard_fn)
rc=$?
set -e
if [[ $rc -eq 0 ]]; then ... fi
```

But the stdout-based check is almost always cleaner, because the
function's "yes" path is the one that produced output anyway.

## Verification

Minimal repro to confirm the bug on a given bash:

```bash
$ bash -c 'set -euo pipefail
fn() { return 1; }
OUT=$(fn)
echo "reached this line"'
$ echo "outer exit: $?"
outer exit: 1
```

If "reached this line" prints, the bash version doesn't propagate the
command-substitution exit through the assignment, and the bug doesn't
apply. If "reached this line" does NOT print and outer exit is 1, the
script silently dies at the assignment — your real script has the same
trap.

Apply the `|| true` fix, run the repro again, and "reached this line"
should print.

## Example

The BB watchdog `case` arm before the fix:

```bash
restart-gateway)
  log "GATEWAY BB PLUGIN DEAD: ${REASON}"          # logged ✓
  $NODE -e "..."                                    # ran ✓
  RUNNING_JOBS=$(cron_job_running)                  # script dies here
  if [[ $? -eq 0 ]]; then
    log "DEFER: ..."                                # never logged
  else
    log "ACTION: Restarting gateway only ..."       # never logged
    launchctl kickstart -k "gui/$(id -u)/ai.openclaw.gateway"
  fi
  ;;
```

Watchdog log over a 12-hour window:

```
[2026-05-11 07:08:18] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 08:03:24] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 10:06:03] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 10:50:57] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 11:46:11] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 12:13:48] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 18:13:42] GATEWAY BB PLUGIN DEAD: ...
[2026-05-11 18:46:24] GATEWAY BB PLUGIN DEAD: ...
```

Seven "DEAD" detections. Zero "ACTION: Restarting" or "DEFER" lines.
The gateway never actually restarted — the script silently aborted at
each `$(cron_job_running)` call. PID confirmed alive continuously
across the entire window.

After the fix (`$(cron_job_running || true)` + non-empty output check),
the next watchdog cycle ran to completion — combined with a separate
detection-correctness fix, the false-positive log line stopped firing
entirely.

## Notes

- This behavior varies by bash version. On some platforms `set -e`
  does NOT propagate command-substitution exits through assignments;
  on others it does. The bash `inherit_errexit` shopt (4.4+) was
  introduced specifically to make this controllable, but the default
  behavior is bash-version- and `BASH_COMPAT`-dependent. Don't assume
  what you tested on Linux applies to macOS bash 3.2 or vice versa.
- The bug is especially nasty inside `case` arms because the arm-end
  `;;` looks like a normal control-flow boundary — there's no syntactic
  hint that a line in the middle might silently exit.
- Bash arithmetic expressions have a related trap:
  `((counter++))` returns exit 1 when `counter` was 0, killing scripts
  under `set -e`. Use `: $((counter++))` or `counter=$((counter + 1))`
  to dodge that one.
- Standard advice "always use `set -euo pipefail`" needs the corollary
  "and audit every `$()` call for guard-function patterns where exit
  1 is a normal return."
- `bash -x` is the fastest diagnostic — when the trace stops mid-block
  with no `+ exit` line and no error, this is what you're looking at.

## References

- POSIX shell `set -e` semantics: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#set
- Bash `set -e` quirks (BashFAQ #105): https://mywiki.wooledge.org/BashFAQ/105
- `inherit_errexit` shopt: https://www.gnu.org/software/bash/manual/html_node/The-Shopt-Builtin.html
