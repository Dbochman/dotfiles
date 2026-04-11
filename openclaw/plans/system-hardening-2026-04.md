# System Hardening Sprint — April 2026

## Status: COMPLETE

## Overview

Address the highest-impact reliability, deployment, and automation gaps found by a 4-agent audit of the OpenClaw system. Organized into 6 parallel workstreams, each small enough for one sub-agent.

---

## Workstream 1: LaunchAgent Hardening

**Agent:** `launchagent-hardener`
**Files to modify:** All 27 plists in `openclaw/launchagents/`

### Tasks

#### 1A. Add HOME env var to 8 agents missing it
These agents have no `EnvironmentVariables` or are missing `HOME`:
- `ai.openclaw.dog-walk-dashboard.plist`
- `ai.openclaw.nest-dashboard.plist`
- `ai.openclaw.usage-dashboard.plist`
- `ai.openclaw.financial-dashboard.plist`
- `ai.openclaw.roomba-dashboard.plist`
- `ai.openclaw.home-dashboard.plist`
- `com.openclaw.bt-connect.plist`
- `com.openclaw.poke-messages.plist`
- `ai.openclaw.home-state-snapshot.plist`

Add to each (or add to existing EnvironmentVariables dict):
```xml
<key>EnvironmentVariables</key>
<dict>
  <key>HOME</key>
  <string>/Users/dbochman</string>
</dict>
```
For agents that already have an EnvironmentVariables dict, just add the HOME key.

#### 1B. Add ProcessType:Background to non-critical agents
Add to all dashboards, snapshots, and presence agents (NOT gateway):
```xml
<key>ProcessType</key>
<string>Background</string>
```

Agents to add this to:
- All 6 dashboard plists (`nest-dashboard`, `usage-dashboard`, `financial-dashboard`, `dog-walk-dashboard`, `roomba-dashboard`, `home-dashboard`)
- All snapshot plists (`nest-snapshot`, `usage-snapshot`, `8sleep-snapshot`, `home-state-snapshot`)
- All presence plists (`presence-cabin`, `presence-crosstown`, `presence-receive`)
- `vacancy-actions`, `bb-watchdog`, `bb-lag-summary`, `cielo-refresh`, `poke-messages`, `bt-connect`
- NOT: `ai.openclaw.gateway.plist` (keep foreground priority)
- NOT: `ai.openclaw.dog-walk-listener.plist` (time-sensitive GPS monitoring)

#### 1C. Add RunAtLoad to dog-walk-listener
`ai.openclaw.dog-walk-listener.plist` has `KeepAlive: true` but no `RunAtLoad`. Add:
```xml
<key>RunAtLoad</key>
<true/>
```

#### 1D. Redirect /dev/null logging to real log files
These 6 agents log to `/dev/null` — redirect to `~/.openclaw/logs/`:
| Agent | New stdout path | New stderr path |
|-------|----------------|-----------------|
| `com.openclaw.bb-watchdog.plist` | `/Users/dbochman/.openclaw/logs/bb-watchdog.log` | same |
| `com.openclaw.bb-lag-summary.plist` | `/Users/dbochman/.openclaw/logs/bb-lag-summary.log` | same |
| `com.openclaw.presence-cabin.plist` | `/Users/dbochman/.openclaw/logs/presence-cabin.log` | same |
| `com.openclaw.presence-crosstown.plist` | `/Users/dbochman/.openclaw/logs/presence-crosstown.log` | same |
| `com.openclaw.presence-receive.plist` | `/Users/dbochman/.openclaw/logs/presence-receive.log` | same |
| `com.openclaw.vacancy-actions.plist` | `/Users/dbochman/.openclaw/logs/vacancy-actions.log` | same |

Use combined stdout+stderr (same file for both) — this matches the existing convention in the repo.

### Completion criteria
- All plists pass `plutil -lint`
- No agent sends output to `/dev/null`
- All non-gateway agents have `ProcessType: Background`
- All agents have HOME in EnvironmentVariables

---

## Workstream 2: Deployment Pipeline Fixes

**Agent:** `deployment-fixer`
**Files to modify:** `openclaw/bin/dotfiles-pull.command`

### Tasks

#### 2A. Replace hardcoded wrapper list with glob
Change the wrapper deployment loop from:
```bash
for wrapper in cielo roomba crosstown-roomba 8sleep mysa petlibro litter-robot crisismode ring; do
```
To a glob that deploys all executable non-extension files:
```bash
for wrapper in "$BIN_SRC"/*; do
  fname=$(basename "$wrapper")
  # Skip files with extensions (scripts deployed separately) and non-executables
  case "$fname" in
    *.py|*.sh|*.command|*.md|*.json|*.yaml) continue ;;
  esac
  [ -x "$wrapper" ] || continue
  cp "$wrapper" "$BIN_DST/$fname"
  chmod +x "$BIN_DST/$fname"
  WRAPPER_DEPLOYED=$((WRAPPER_DEPLOYED + 1))
done
```

#### 2B. Add workspace scripts deployment
After the workspace markdown deployment section, add:
```bash
# Deploy workspace scripts
SCRIPTS_SRC="$REPO/openclaw/workspace/scripts"
SCRIPTS_DST="$HOME/.openclaw/workspace/scripts"
if [ -d "$SCRIPTS_SRC" ] && [ -d "$SCRIPTS_DST" ]; then
  SCRIPTS_DEPLOYED=0
  for script in "$SCRIPTS_SRC"/*; do
    [ -f "$script" ] || continue
    fname=$(basename "$script")
    cp "$script" "$SCRIPTS_DST/$fname"
    chmod +x "$SCRIPTS_DST/$fname"
    SCRIPTS_DEPLOYED=$((SCRIPTS_DEPLOYED + 1))
  done
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed $SCRIPTS_DEPLOYED scripts" >> "$LOG"
fi
```

#### 2C. Add error handling with set -e and trap
Add near the top of the script (after the LOG/REPO vars):
```bash
set -euo pipefail
trap 'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FATAL: dotfiles-pull failed at line $LINENO" >> "$LOG"' ERR
```
**Exception**: The git stash/pull section should use explicit error checks rather than `set -e` since stash operations can "fail" benignly. Wrap that section in `set +e` / `set -e`.

#### 2D. Update smoke test to match glob deployment
Replace the hardcoded smoke test list with the same glob logic, or at minimum add the missing commands (`fi-collar`, `nest`, etc.) to the existing list.

### Completion criteria
- `dotfiles-pull.command` deploys all executable wrappers via glob
- Workspace scripts are deployed
- Script exits on fatal errors with a log entry
- Smoke test covers all deployed wrappers

---

## Workstream 3: Cron Job Cleanup

**Agent:** `cron-fixer`
**Files to modify:** `openclaw/cron/jobs.json`

### Tasks

#### 3A. Remove past-due one-shot jobs
Delete these job entries entirely (they already fired on Mini):
- `datenight-apr-italian` (was April 1)
- `doubledate-q2-apr-thai` (was April 1)

#### 3B. Stagger colliding timestamps
These pairs fire at identical times — offset double-dates by 2 hours:
| Date | Job to move | New time |
|------|-------------|----------|
| 2026-07-01 | `doubledate-q3-jul-korean` | 14:00 UTC (was 12:00) |
| 2026-10-01 | `doubledate-q4-oct-mexican` | 14:00 UTC (was 12:00) |

Update `nextRunAtMs` for the moved jobs accordingly.

#### 3C. Strip stale runtime state from recurring jobs
For the 3 recurring jobs (`gws-julia-morning-briefing-0001`, `gws-dylan-morning-briefing-0001`, `weekly-report-0001`), remove the `state` block entirely from the dotfiles copy. The Mini's cron subsystem manages state — the dotfiles copy should only have job definitions. Specifically remove `lastRunAtMs`, `lastRunStatus`, `lastDurationMs`, and `nextRunAtMs` fields from these jobs.

**IMPORTANT**: Only strip state from the 3 recurring jobs. One-shot jobs (`deleteAfterRun: true`) need their `nextRunAtMs` to know when to fire.

#### 3D. Fix hardcoded UTC offset in Julia briefing
In `gws-julia-morning-briefing-0001`, the prompt hardcodes `-04:00`. Change the instruction text from:
```
timeMin: "<TODAY>T00:00:00-04:00"
```
To instruct the agent to compute dynamically:
```
Use America/New_York timezone for timeMin/timeMax boundaries (UTC-5 during EST Nov-Mar, UTC-4 during EDT Mar-Nov).
```

### Completion criteria
- Past-due one-shot jobs removed
- No colliding timestamps
- Recurring jobs have no stale state in dotfiles
- Julia briefing uses dynamic timezone

---

## Workstream 4: Crosstown Routines Enhancement

**Agent:** `routines-enhancer`
**Files to modify:** `openclaw/skills/crosstown-routines/SKILL.md`

### Tasks

#### 4A. Add Cielo AC to Goodnight, Away, Welcome Home
Add `cielo` commands to routines:

**Goodnight** — add after thermostat step:
```
5. Set AC to sleep mode: `cielo set bedroom --mode sleep` (if AC is on)
```

**Away** — add after thermostat step:
```
5. Turn off all AC units: `cielo off all`
```

**Welcome Home** — add after thermostat step:
```
6. Restore AC to comfortable: `cielo on living --mode cool --temp 72` (summer) or skip (winter)
```

Note: The agent should check season/weather context before blindly turning on AC. Add a note: "Skip Cielo commands in winter months (Nov-Mar) unless user specifically requests AC."

#### 4B. Add August lock to Goodnight and Away
**Goodnight** — add as final step:
```
6. Lock front door: `august lock`
```

**Away** — add before Roombas:
```
4. Lock front door: `august lock`
```

#### 4C. Add Samsung TV to Movie Night and Goodnight
**Movie Night** — add before speaker volume:
```
3. Wake TV and set to art mode input: `samsung-tv power on` then `samsung-tv input HDMI1`
```

**Goodnight** — add:
```
5. Turn off TV: `samsung-tv power off`
```

#### 4D. Update allowed-tools and metadata
Update the YAML frontmatter:
```yaml
allowed-tools: Bash(hue:*) Bash(nest:*) Bash(speaker:*) Bash(crosstown-roomba:*) Bash(cielo:*) Bash(august:*) Bash(samsung-tv:*)
metadata: {"openclaw":{"emoji":"H","requires":{"bins":["hue","nest","crosstown-roomba","cielo","august","samsung-tv"]}}}
```

#### 4E. Update the Quick Reference table
Add Cielo, Lock, and TV columns to the summary table at the bottom.

### Completion criteria
- All 5 routines updated with Cielo/Lock/TV where appropriate
- Frontmatter `allowed-tools` and `metadata.requires.bins` updated
- Quick Reference table matches actual routine steps
- Skill Boundaries section updated to reference cielo-ac, august-lock, samsung-tv skills

---

## Workstream 5: Stale Content Cleanup

**Agent:** `stale-cleaner`
**Files to modify:** Multiple small fixes across skills

### Tasks

#### 5A. Fix places skill home location
In `openclaw/skills/places/SKILL.md`, find the line referencing "Newton, MA (42.33, -71.21)" and update to "West Roxbury, MA (42.28, -71.16)" (19 Crosstown Ave coordinates).

Search the file for any other Newton references and update them. The search examples on line 15-16 reference Newton — update to West Roxbury/Brookline as appropriate.

#### 5B. Remove cabin-roomba dead code
Delete the entire `openclaw/skills/cabin-roomba/` directory. It has no SKILL.md and only contains a stale `irobot-cloud.py` from a deprecated approach. The active cabin Roomba skill is at `openclaw/skills/roomba/`.

#### 5C. Remove stale top-level dotfiles-pull.command
Delete `openclaw/dotfiles-pull.command` (the 45-line stub). The real script is `openclaw/bin/dotfiles-pull.command` (139 lines). Having both is confusing.

#### 5D. Fix restaurant-snipe BB curl (missing password)
In `openclaw/skills/restaurant-snipe/SKILL.md`, find the curl call to BlueBubbles and add the `?password=$BLUEBUBBLES_PASSWORD` query parameter.

#### 5E. Fix 8sleep stale config path reference
In `openclaw/skills/8sleep/SKILL.md`, find the reference to `~/.config/eightctl/config.yaml` and update to reference the actual Python wrapper's config path (`~/.openclaw/8sleep/config.json` or whatever the actual path is — read the SKILL.md to determine the correct path).

### Completion criteria
- No references to Newton as home location in places skill
- cabin-roomba directory deleted
- Stale dotfiles-pull.command removed
- Restaurant snipe curl has BB password
- 8sleep config reference is accurate

---

## Workstream 6: Plan README Update

**Agent:** `plan-updater` (run LAST, after all others complete)
**Files to modify:** `openclaw/plans/README.md`

### Tasks

#### 6A. Add this plan to the Active table
Add a row to the Active plans table:
```
| [system-hardening-2026-04](system-hardening-2026-04.md) | Multi-agent reliability, deployment, and automation hardening sprint |
```

#### 6B. Move completed plans to archive if applicable
Check if any currently-listed Active plans should be moved to Archive (if their status is now Complete).

### Completion criteria
- README.md reflects current plan state

---

## Agent Assignment Summary

| # | Workstream | Agent | Isolation | Files | Est. Edits |
|---|-----------|-------|-----------|-------|------------|
| 1 | LaunchAgent Hardening | `launchagent-hardener` | worktree | 18 plists | ~50 edits |
| 2 | Deployment Pipeline | `deployment-fixer` | worktree | 1 file | ~4 edits |
| 3 | Cron Job Cleanup | `cron-fixer` | worktree | 1 file | ~4 edits |
| 4 | Routines Enhancement | `routines-enhancer` | worktree | 1 file | ~5 edits |
| 5 | Stale Cleanup | `stale-cleaner` | worktree | 5 files | ~5 edits |
| 6 | Plan README | `plan-updater` | main | 1 file | 1 edit |

Workstreams 1-5 run in parallel. Workstream 6 runs after all complete.

---

## Progress Tracker

_Updated by agents as they work._

| Workstream | Status | Agent | Notes |
|-----------|--------|-------|-------|
| 1. LaunchAgent Hardening | DONE | direct | HOME env (8 agents), ProcessType:Background (20 agents), RunAtLoad (dog-walk), /dev/null→logs (6 agents). All 26 plists pass lint. |
| 2. Deployment Pipeline | DONE | direct | Error handling, glob-based wrappers, workspace scripts deployment, dynamic smoke test |
| 3. Cron Job Cleanup | DONE | direct | Removed 2 past-due jobs, staggered 2 collisions (+2h), stripped state from 3 recurring, fixed hardcoded TZ offset |
| 4. Routines Enhancement | DONE | direct | Added Cielo AC, August lock, Samsung TV to all routines. Updated frontmatter + quick reference table. |
| 5. Stale Cleanup | DONE | direct | Places→West Roxbury, trashed cabin-roomba + stale dotfiles-pull, fixed BB curl password, fixed 8sleep config path |
| 6. Plan README | DONE | direct | Added plan to Active table |
