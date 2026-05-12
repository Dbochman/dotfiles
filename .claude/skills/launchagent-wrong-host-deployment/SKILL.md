---
name: launchagent-wrong-host-deployment
description: |
  Diagnose macOS LaunchAgents that are loaded on the "wrong" host —
  the .plist file lives in ~/Library/LaunchAgents/ and is registered,
  but its ProgramArguments / EnvironmentVariables / StandardOutPath /
  StandardErrorPath / WorkingDirectory hardcode paths that only exist
  on a different machine. Use when: (1) `launchctl list` shows a
  LaunchAgent with `last exit code = 78` (EX_CONFIG) or `32256`
  (wait-status for exit-126) that never produces any log output and
  has no successful runs, (2) the .plist references paths like
  `/Users/<somebody-else>/...` or hostnames in another machine's
  namespace, (3) deployment docs say "should only be on machine X"
  but the agent is loaded on machine Y, (4) the agent fires on
  schedule and exits silently — no script-level log lines because
  launchd dies at the stderr-bind step before exec, (5) a dotfiles
  or ansible deploy step copies all `.plist` files indiscriminately
  to every host without filtering by hostname/user. The fix is
  three-step: bootout on the wrong host, remove the .plist from
  ~/Library/LaunchAgents/, and either filter the deploy step or
  delete the plist from the source repo if it's truly retired.
author: Claude Code
version: 1.0.0
date: 2026-05-12
---

# LaunchAgent loaded on the wrong host

## Problem

A `.plist` is registered on host A but its content paths only resolve
on host B. Typical symptoms:

- `launchctl list <label>` shows `last exit code = 78` (`EX_CONFIG`).
- The plist's `StandardErrorPath` / `StandardOutPath` references a
  user directory that doesn't exist (e.g., `/Users/juliajoy/...` on
  a Mac where the only user is `dbochman`).
- The agent's log file at the configured path doesn't exist and
  was never created — launchd couldn't open it.
- The script the plist invokes was never reached; nothing in any
  log file indicates the script itself ran. There's no failure
  trace because the failure is at launchd's spawn-prep step.

This pattern has bitten the dotfiles repo three times in one week:

| Agent | Wrong-host symptom |
|---|---|
| `ai.openclaw.usage-token-push` | Mini→Mini self-loop SSH, exit 255 |
| `com.openclaw.presence-crosstown` | ARP-scanning a subnet the Mini wasn't on |
| `com.openclaw.gas-scrape` + `water-scrape` | `/Users/juliajoy/...` paths on Dylan's Mini, exit 78 |

The underlying cause was the same each time: a dotfiles deploy step
that copies every `.plist` file under `dotfiles/openclaw/launchagents/`
into the target machine's `~/Library/LaunchAgents/` without filtering
by hostname or user. The plist content was correct for its intended
host but landed on the wrong one.

## Context / Trigger Conditions

- `launchctl list` shows `last exit code = 78` (EX_CONFIG) or
  `32256` (256 × 126 = "command found but not executable / No such
  file or directory") for the agent
- The `StandardOutPath` and `StandardErrorPath` files don't exist
  on disk, and `ls` on the parent dir confirms the user namespace
  isn't this host's
- The agent's documented purpose in your repo's `LAUNCHAGENTS.md`
  (or equivalent inventory) says it belongs on a different machine
- `pgrep -fl <script>` returns nothing despite the agent being
  registered and firing on schedule (the script never starts)
- No entries in the script's own log file (because launchd dies
  before exec)

## Solution

Three-part cleanup:

### 1. Bootout + remove on the wrong host

```bash
ssh <wrong-host> '
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
  rm ~/Library/LaunchAgents/<label>.plist
  # If the script was also deployed, remove it too:
  rm -f ~/.openclaw/bin/<script>.sh
'
```

If the agent is supposed to exist on the correct host but is just
misplaced, scp the plist to the right host instead of removing the
content from the repo.

### 2. Update the deploy step to filter

The dotfiles-pull / ansible / chef step that copies plists should
not copy every `.plist` to every host. Options:

```bash
# Approach A: filename prefix per host
~/dotfiles/openclaw/launchagents/dylans-mac-mini/<label>.plist
~/dotfiles/openclaw/launchagents/julias-mbp/<label>.plist
# Deploy step: cp dotfiles/.../$HOSTNAME/* ~/Library/LaunchAgents/

# Approach B: glob exclusion in the deploy script
case "$(hostname -s)" in
  juliajoy-mbp) ;;  # ok to deploy gas-scrape, water-scrape
  dbochman-mini)
    # skip Julia-only agents
    SKIP_GLOB="com.openclaw.gas-scrape|com.openclaw.water-scrape"
    ;;
esac

# Approach C: plist content-aware check
for plist in launchagents/*.plist; do
  # If plist mentions a user dir that doesn't exist on this host, skip
  grep -q "/Users/$(whoami)/" "$plist" || continue
  cp "$plist" ~/Library/LaunchAgents/
done
```

### 3. If the agent is truly retired, delete from the repo

```bash
git rm dotfiles/openclaw/launchagents/<label>.plist
git rm dotfiles/openclaw/bin/<associated-script>.sh
$EDITOR dotfiles/openclaw/LAUNCHAGENTS.md   # remove the inventory row
```

This prevents the next dotfiles-pull from re-deploying it. Without
this step, your cleanup on host A gets undone the next time the
deploy runs.

## Verification

```bash
# 1. Agent is gone from launchctl
ssh <host> 'launchctl list | grep <label>'   # → empty

# 2. plist file is gone from ~/Library/LaunchAgents/
ssh <host> 'ls ~/Library/LaunchAgents/ | grep <label>'   # → empty

# 3. Repo is clean (if retiring)
grep -rn '<label>' dotfiles/openclaw/   # → empty or archive-only

# 4. Next deploy doesn't resurrect it
ssh <host> 'bash ~/.openclaw/bin/dotfiles-pull.command'
ssh <host> 'ls ~/Library/LaunchAgents/ | grep <label>'   # → still empty
```

## Example

From the 2026-05-12 gas-scrape investigation:

```
$ ssh dylans-mac-mini 'launchctl print gui/$(id -u)/com.openclaw.gas-scrape | grep -E "last exit|path"'
    path = /Users/dbochman/Library/LaunchAgents/com.openclaw.gas-scrape.plist
    stdout path = /Users/juliajoy/.openclaw/logs/gas-scrape.log
    stderr path = /Users/juliajoy/.openclaw/logs/gas-scrape.err.log
    last exit code = 78: EX_CONFIG
```

Mismatch is immediately visible: plist is registered on `dbochman`,
but stdout/stderr land in `/Users/juliajoy/`. launchd can't open the
stderr file → EX_CONFIG → 78.

```
$ ssh dylans-mac-mini 'ls /Users/juliajoy/.openclaw/logs/ 2>&1'
ls: /Users/juliajoy/: No such file or directory
```

Confirmed: that user namespace doesn't exist on this host. The plist
was correct content-for-Julia, wrong host. Three-part fix applied:
bootout + plist deletion on Mini, plist deletion from dotfiles repo
(this agent was retired by a newer OpenClaw cron job), inventory row
removed from LAUNCHAGENTS.md.

## Notes

- macOS doesn't warn at `launchctl bootstrap` time about path
  mismatches — the bootstrap succeeds, the agent appears in the list,
  and only on first fire does EX_CONFIG show up.
- Always check the **plist's stdout/stderr/working-dir paths**, not
  just the ProgramArguments. The ProgramArguments script might exist
  on the wrong host (because dotfiles deploy copies it there too),
  but its env vars and log paths can still leak the intended host.
- Exit code 78 is launchd-specific signaling for "configuration
  error." Other commonly-seen EX_* codes from `sysexits.h` that show
  up in `last exit code`:
  - 64 = EX_USAGE (command-line misuse)
  - 67 = EX_NOUSER (user doesn't exist)
  - 70 = EX_SOFTWARE (internal software error)
  - 73 = EX_CANTCREAT (can't create output file)
  - 78 = EX_CONFIG (configuration error)
- The `last exit code` reported by `launchctl print` is the **shell
  exit code**, but if it's a multiple of 256 (e.g. 32256, 65280),
  divide by 256 — that's `wait(2)` status. `32256 / 256 = 126`
  ("command not executable"); `65280 / 256 = 255` ("script failed
  catastrophically" or signal-style exit).
- Adjacent skill `homebrew-cellar-versioned-path-breakage` covers
  a related but distinct pattern: same `last exit code = 32256`
  shape, but from versioned Homebrew Cellar paths in
  ProgramArguments instead of cross-host user paths. If you see
  exit 32256, check both.

## References

- macOS launchd `sysexits.h` exit codes:
  https://opensource.apple.com/source/Libc/Libc-1158.30.7/include/sysexits.h.auto.html
- launchd plist reference (`launchd.plist(5)`):
  https://www.manpagez.com/man/5/launchd.plist/
