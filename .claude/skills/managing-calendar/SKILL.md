---
name: managing-calendar
description: Manage Microsoft Outlook calendar via CLI. View schedule, create events, open events, respond to invites, update meetings. Use when working with calendar, scheduling meetings, checking availability, or managing events.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: workflows/ for detailed procedures

Related skills:
- managing-outlook-email: For email operations
- reading-meeting-transcripts: For Teams meeting transcripts

Shared resources (in managing-outlook-email):
- Installation: https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/
-->

# Calendar Management

Manage Microsoft Outlook calendar via `calendar-cli` - view schedule, create events, respond to invites, and manage meetings.

## Verify Installation

```bash
# Check tool is available
calendar-cli --version

# Test authentication (will prompt for login if needed)
calendar-cli find --after today --limit 1 --toon
```

If command not found, see [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/). For email operations, see [managing-outlook-email](../managing-outlook-email/SKILL.md). For meeting transcripts, see [reading-meeting-transcripts](../reading-meeting-transcripts/SKILL.md).

## When to Use This Skill

Use this skill when users want to:

- **View schedule**: Today's events, upcoming week, specific dates
- **Create meetings**: Schedule events with attendees
- **Respond to invites**: Accept, decline, or tentatively accept
- **Update events**: Change time, location, attendees
- **Manage calendars**: List calendars, check for conflicts
- **Protect focus time**: Analyze and reorganize schedule

## Quick Start Examples

### View Schedule
```bash
# Today's events
calendar-cli find --after 2025-02-06 --before 2025-02-07 --toon

# This week
calendar-cli find --after 2025-02-06 --before 2025-02-13 --toon

# With relative times ("2 hours ago", "in 30 minutes")
calendar-cli find --after today --relative --toon

# Specific calendar
calendar-cli find --after today --calendar "Work" --toon
```

### Get Event Details
```bash
# Full event details (HTML converted to markdown)
calendar-cli get <event-id> --toon

# With specific timezone
calendar-cli get <event-id> --timezone "America/New_York" --toon

# Raw HTML body
calendar-cli get <event-id> --no-markdown --toon
```

### Open in Outlook
```bash
# Open event in browser (Outlook on the web)
calendar-cli open --event-id <event-id>

# Print URL without opening browser
calendar-cli open --event-id <event-id> --no-browser
```

### Create Events
```bash
# Basic meeting
calendar-cli create \
  --subject "Team Sync" \
  --start "2025-02-10T14:00:00" \
  --end "2025-02-10T15:00:00" \
  --toon

# With attendees
calendar-cli create \
  --subject "Project Review" \
  --start "2025-02-10T14:00:00" \
  --end "2025-02-10T15:00:00" \
  --attendees "alice@company.com,bob@company.com" \
  --toon

# With location and body
calendar-cli create \
  --subject "Planning Session" \
  --start "2025-02-10T10:00:00" \
  --end "2025-02-10T11:00:00" \
  --location "Conference Room A" \
  --body "Agenda:\n1. Review goals\n2. Assign tasks" \
  --toon
```

### Respond to Invites
```bash
# Accept
calendar-cli respond <event-id> accept --toon

# Decline
calendar-cli respond <event-id> decline --toon

# Tentative
calendar-cli respond <event-id> tentative --toon

# With message
calendar-cli respond <event-id> accept --message "Looking forward to it!" --toon
```

### Update Events
```bash
# Change time
calendar-cli update <event-id> --start "2025-02-10T15:00:00" --toon

# Update subject
calendar-cli update <event-id> --subject "Updated: Team Sync" --toon

# Add attendees
calendar-cli update <event-id> --attendees "carol@company.com" --toon
```

### Delete Events
```bash
calendar-cli delete <event-id> --toon
```

### List Calendars
```bash
calendar-cli calendars --toon
```

Run `calendar-cli --help` for all commands and flags. Run `calendar-cli <command> --help` for detailed options.

## Workflows

1. **Calendar Review** ([workflows/calendar-review.md](workflows/calendar-review.md))
   - View today and upcoming events
   - Identify conflicts and gaps

2. **Schedule Meeting** ([workflows/schedule-meeting.md](workflows/schedule-meeting.md))
   - Gather requirements
   - Check conflicts, create event

3. **Focus Time Management** ([workflows/focus-time.md](workflows/focus-time.md))
   - Analyze schedule for gaps
   - Protect deep work blocks

4. **Category Prioritization** ([workflows/category-prioritization.md](workflows/category-prioritization.md))
   - Color-code events by priority
   - Visual calendar organization

5. **Open Loops Review** ([workflows/open-loops-review.md](workflows/open-loops-review.md))
   - Check pending invites
   - Cross-references flagged emails

## Troubleshooting

**Authentication errors:**
```bash
rm ~/.ai-pim-utils/auth-cache
calendar-cli find --limit 1  # Triggers re-authentication
```

**Command not found:**
- macOS/Linux: Add `~/.local/bin` to PATH
- Windows: Restart PowerShell after installation

**Event creation fails:**
- Ensure `--start` and `--end` are provided
- Use ISO 8601 format: `2025-02-10T14:00:00`
- Check attendee email format

**See:** [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/) for detailed troubleshooting.
