# Eight Sleep Morning Briefing — DONE

## Status: RESOLVED (2026-04-04) — `openclaw cron --tools exec` unblocked exec in isolated sessions

## Goal

Add Eight Sleep sleep summaries (score, duration, REM%, deep%, snoring) to Dylan's and Julia's morning briefing cron jobs.

## Resolution

OpenClaw v2026.4.1 added `--tools <csv>` flag for per-job tool allowlists. Adding `--tools exec` to the briefing cron jobs allows the isolated agent to invoke shell commands.

**Two key findings:**
1. `--tools exec` makes the exec tool *available* but the agent won't use bare CLI names — `~/.openclaw/bin` is NOT on the isolated session's PATH
2. Must use **full absolute paths** in the prompt: `/Users/dbochman/.openclaw/bin/8sleep sleep dylan`

**Applied to both briefings:**
- Dylan (`gws-dylan-morning-briefing-0001`, 8AM ET): `toolsAllow: ["exec"]`, Step 1 runs `/Users/dbochman/.openclaw/bin/8sleep sleep dylan`
- Julia (`gws-julia-morning-briefing-0001`, 7AM ET): `toolsAllow: ["exec"]`, Step 1.5 runs `/Users/dbochman/.openclaw/bin/8sleep sleep julia`

When no sleep data is available (at cabin, pod off), the command returns "No sleep data available" and the agent silently omits the sleep section.

## What Failed Previously (v2026.2.21)

Isolated cron agents had no exec tool at all. Four approaches tried, all failed:
1. "Use the 8sleep skill" — agent never invoked it
2. "Run via Bash" — ignored
3. "Read file /tmp/8sleep-*.txt" — ignored
4. Prompt injection at runtime — over-engineered, abandoned

Root cause was confirmed: isolated sessions had a limited tool set with no exec access. The `--tools` flag in v2026.4.1 was the fix.
