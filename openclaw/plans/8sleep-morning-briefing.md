# Eight Sleep Morning Briefing — Blocked

## Status: BLOCKED (isolated cron agents can't invoke Bash/file-read tools)

## Goal

Add Eight Sleep sleep summaries (score, duration, REM%, deep%, snoring) to Dylan's and Julia's morning briefing cron jobs.

## What Works

- `8sleep sleep dylan` CLI returns clean data
- Snapshot script (`openclaw/bin/8sleep-snapshot.sh`) captures both sides to `/tmp/8sleep-{dylan,julia}-latest.txt`
- LaunchAgent plist (`ai.openclaw.8sleep-snapshot`) runs at 6:50 AM daily — tested and working
- Data correctly appears/disappears based on Crosstown presence (Julia at cabin = no file)

## What Doesn't Work

OpenClaw cron agent in `sessionTarget: "isolated"` mode consistently ignores non-GWS instructions. Tried 4 approaches, all failed:

1. "Use the 8sleep skill to run `8sleep sleep dylan`" — agent never invoked the skill
2. "Run the command `8sleep sleep dylan` (via Bash)" — same, ignored
3. "Read the file `/tmp/8sleep-dylan-latest.txt`" as a preamble — agent still skipped it
4. Started building jobs.json injection (snapshot script modifies prompt at runtime) — rejected as over-engineered

## Root Cause Hypothesis

The isolated cron agent likely has a limited tool set (GWS skills only, no general Bash or file-read tool). The prompt instructions to use Bash or read files are impossible for the agent to act on.

## Next Steps

Before retrying, investigate:
- What tools are actually available to `sessionTarget: "isolated"` cron agents
- Option (a): inject data directly into the prompt payload before the job runs
- Option (b): switch to `sessionTarget: "shared"` so it has access to the full agent tool set

The snapshot script and plist are ready in the dotfiles repo — just need a viable injection mechanism.
