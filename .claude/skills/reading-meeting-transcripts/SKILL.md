---
name: reading-meeting-transcripts
description: Access Microsoft Teams meeting transcripts via CLI. Read transcripts from calendar events, extract speaker dialogue, summarize discussions. Use when reading meeting notes, reviewing what was said, extracting action items, or analyzing meeting participation.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: workflows/ for detailed procedures

Related skills:
- managing-outlook-email: For email operations
- managing-calendar: For calendar and scheduling (find event IDs here)

Shared resources (in managing-outlook-email):
- Installation: https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/
-->

# Meeting Transcript Access

Access Microsoft Teams meeting transcripts via `transcript-cli` - read transcripts, extract speaker dialogue, and analyze meeting content.

## Verify Installation

```bash
# Check tool is available
transcript-cli --version

# Authentication is shared with other tools
# If not authenticated, any command will prompt for login
```

If command not found, see [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/). For calendar events (to get event IDs), see [managing-calendar](../managing-calendar/SKILL.md). For email, see [managing-outlook-email](../managing-outlook-email/SKILL.md).

## When to Use This Skill

Use this skill when users want to:

- **Read meeting transcripts**: Access what was said in Teams meetings
- **Summarize discussions**: Extract key points from meetings
- **Find action items**: Identify commitments and decisions
- **Analyze participation**: See who spoke and when
- **Review meeting history**: Track topic evolution over time
- **Self-reflection**: Analyze communication patterns

## Quick Start Examples

### Read Transcript from Calendar Event
```bash
# Simplest: use calendar event ID directly
transcript-cli read --event-id <event-id> --toon

# Find event ID from calendar first
calendar-cli find --after 2025-02-01 --subject "Team Sync" --toon
# Then read transcript
transcript-cli read --event-id <event-id-from-above> --toon
```

### Find Transcripts
```bash
# Find transcripts for a calendar event
transcript-cli find --event-id <event-id> --toon

# If meeting has multiple transcripts (e.g., recurring)
transcript-cli find --event-id <event-id> --toon
# Then read specific one by index
transcript-cli read --event-id <event-id> --transcript-index 1 --toon
```

### Alternative: Use Join URL
```bash
# If you have the Teams join URL instead of event ID
transcript-cli find --join-url "https://teams.microsoft.com/l/meetup-join/..." --toon
transcript-cli read --join-url "https://teams.microsoft.com/l/meetup-join/..." --toon
```

### Export Raw VTT
```bash
# Get raw VTT format (for external processing)
transcript-cli read --event-id <event-id> --raw
```

Run `transcript-cli --help` for all commands and flags. Run `transcript-cli <command> --help` for detailed options.

## Workflows

1. **Read Meeting Transcript** ([workflows/transcript-workflow.md](workflows/transcript-workflow.md))
   - Find meeting from calendar
   - Read and summarize transcript

2. **Meeting Analytics** ([workflows/meeting-analytics.md](workflows/meeting-analytics.md))
   - Analyze speaker participation
   - Track commitments made

3. **Timeline Synthesis** ([workflows/timeline-synthesis.md](workflows/timeline-synthesis.md))
   - Review topic across multiple meetings
   - Build narrative timeline

4. **Tone Analysis** ([workflows/tone-analysis.md](workflows/tone-analysis.md))
   - Analyze communication patterns
   - Self-reflection on meeting behavior

## Typical Workflow: Calendar → Transcript

Most transcript access starts from a calendar event:

```bash
# 1. Find the meeting on calendar
calendar-cli find --after 2025-02-01 --subject "Project Review" --toon

# 2. Note the event ID from results, then read transcript
transcript-cli read --event-id AAMkAGI2... --toon

# 3. (Optional) If multiple transcripts, list them first
transcript-cli find --event-id AAMkAGI2... --toon
# Then read specific one
transcript-cli read --event-id AAMkAGI2... --transcript-index 0 --toon
```

## Troubleshooting

**No transcript found:**
- Verify meeting had transcription enabled
- Wait a few minutes after meeting ends (processing time)
- Check you have access to the meeting

**Authentication errors:**
```bash
rm ~/.ai-pim-utils/auth-cache
transcript-cli find --event-id <any-event> # Triggers re-authentication
```

**Command not found:**
- macOS/Linux: Add `~/.local/bin` to PATH
- Windows: Restart PowerShell after installation

**Permission denied:**
- You may not have access to external meeting transcripts
- Check with meeting organizer

**See:** [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/) for detailed troubleshooting.
