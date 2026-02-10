---
name: calendar
description: View, create, and manage Google Calendar events for Dylan or Julia. Use when asked about schedule, appointments, meetings, calendar, what's coming up, free time, conflicts, availability, or Julia's calendar.
allowed-tools: Bash(calendar:*)
metadata: {"openclaw":{"emoji":"📅","requires":{"bins":["gog"]}}}
---

# Google Calendar

Access Google Calendar via the `gog` CLI. Credentials are managed via file-based keyring with OAuth2.

## Available Commands

### List calendars
```bash
gog calendar calendars
```

### Today's events
```bash
gog calendar events --today
```

### Tomorrow's events
```bash
gog calendar events --tomorrow
```

### This week's events
```bash
gog calendar events --week
```

### Next N days
```bash
gog calendar events --days=7
```

### Events in a date range
```bash
gog calendar events --from="2026-02-10" --to="2026-02-15"
```
Supports RFC3339 timestamps, dates (YYYY-MM-DD), and relative values: `today`, `tomorrow`, `monday`, etc.

### Events from all calendars
```bash
gog calendar events --today --all
```

### Get a specific event
```bash
gog calendar event <calendarId> <eventId>
```

### Search events
```bash
gog calendar search "meeting" --days=30
gog calendar search "standup" --from="2026-02-01" --to="2026-02-28"
```
Also supports `--today`, `--tomorrow`, `--week`, `--calendar=<id>`, `--max=<n>`.

### Create an event
```bash
gog calendar create primary \
  --summary="Team Standup" \
  --from="2026-02-10T09:00:00-05:00" \
  --to="2026-02-10T09:30:00-05:00" \
  --description="Daily sync" \
  --location="Zoom" \
  --attendees="alice@example.com,bob@example.com"
```
Additional flags: `--all-day`, `--rrule`, `--reminder=popup:15m`, `--event-color=<1-11>`, `--visibility=<default|public|private>`, `--transparency=<busy|free>`, `--with-meet`, `--send-updates=<all|externalOnly|none>`, `--attachment=<url>`.

### Recurring Events

Use `--rrule` with RFC 5545 RRULE format to create recurring events.

#### Common RRULE patterns

| Pattern | RRULE |
|---------|-------|
| Every Monday | `RRULE:FREQ=WEEKLY;BYDAY=MO` |
| Every weekday (Mon–Fri) | `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` |
| Every other Tuesday | `RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU` |
| Daily | `RRULE:FREQ=DAILY` |
| 1st of every month | `RRULE:FREQ=MONTHLY;BYMONTHDAY=1` |
| First Friday of every month | `RRULE:FREQ=MONTHLY;BYDAY=1FR` |
| Yearly on March 15 | `RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=15` |
| Weekly until end of year | `RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231T235959Z` |
| Daily for 10 occurrences | `RRULE:FREQ=DAILY;COUNT=10` |

#### Examples

Weekly standup Mon–Fri at 9am:
```bash
gog calendar create primary \
  --summary="Team Standup" \
  --from="2026-02-10T09:00:00-05:00" \
  --to="2026-02-10T09:15:00-05:00" \
  --rrule="RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" \
  --location="Zoom" \
  --force --no-input
```

Biweekly 1:1 on Tuesdays with Google Meet:
```bash
gog calendar create primary \
  --summary="1:1 with Alice" \
  --from="2026-02-11T14:00:00-05:00" \
  --to="2026-02-11T14:30:00-05:00" \
  --rrule="RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU" \
  --attendees="alice@example.com" \
  --with-meet \
  --send-updates=all \
  --force --no-input
```

Monthly team review, first Friday, ending Dec 2026:
```bash
gog calendar create primary \
  --summary="Monthly Team Review" \
  --from="2026-03-06T15:00:00-05:00" \
  --to="2026-03-06T16:00:00-05:00" \
  --rrule="RRULE:FREQ=MONTHLY;BYDAY=1FR;UNTIL=20261231T235959Z" \
  --force --no-input
```

### Update an event
```bash
gog calendar update primary <eventId> \
  --summary="New Title" \
  --from="2026-02-10T10:00:00-05:00" \
  --to="2026-02-10T10:30:00-05:00"
```
Set any field to empty string to clear it. Supports `--add-attendee` to add without replacing.

For recurring events, use `--scope` with `--original-start` (the original start time of the instance):
- `--scope=single` — update only this occurrence
- `--scope=future` — this and all future occurrences
- `--scope=all` — every occurrence in the series

```bash
# Change the title of a single occurrence
gog calendar update primary <eventId> \
  --summary="Canceled - Team Standup" \
  --scope=single \
  --original-start="2026-02-12T09:00:00-05:00" \
  --force --no-input

# Move all future occurrences to a new time
gog calendar update primary <eventId> \
  --from="2026-02-12T10:00:00-05:00" \
  --to="2026-02-12T10:15:00-05:00" \
  --scope=future \
  --original-start="2026-02-12T09:00:00-05:00" \
  --force --no-input
```

### Delete an event
```bash
gog calendar delete primary <eventId>
```

For recurring events, use `--scope` with `--original-start`:
- `--scope=single` — delete only this occurrence
- `--scope=future` — this and all future occurrences
- `--scope=all` — delete the entire series

```bash
# Delete a single occurrence
gog calendar delete primary <eventId> \
  --scope=single \
  --original-start="2026-02-12T09:00:00-05:00" \
  --force --no-input

# Delete the entire recurring series
gog calendar delete primary <eventId> \
  --scope=all \
  --force --no-input
```

### Respond to an invitation
```bash
gog calendar respond primary <eventId> --status=accepted
gog calendar respond primary <eventId> --status=declined --comment="Out of town"
```
Status options: `accepted`, `declined`, `tentative`, `needsAction`.

### Check free/busy
```bash
gog calendar freebusy primary --from="2026-02-10T00:00:00-05:00" --to="2026-02-10T23:59:59-05:00"
gog calendar freebusy "user1@gmail.com,user2@gmail.com" --from=... --to=...
```

### Find scheduling conflicts
```bash
gog calendar conflicts --today
gog calendar conflicts --week
gog calendar conflicts --days=14
gog calendar conflicts --calendars="primary,work@company.com"
```

### Create Focus Time block
```bash
gog calendar focus-time \
  --summary="Deep Work" \
  --from="2026-02-10T14:00:00-05:00" \
  --to="2026-02-10T16:00:00-05:00" \
  --auto-decline=all \
  --chat-status=doNotDisturb
```

### Create Out of Office event
```bash
gog calendar out-of-office \
  --summary="Vacation" \
  --from="2026-03-01" \
  --to="2026-03-08" \
  --all-day \
  --auto-decline=all
```
Alias: `gog calendar ooo`.

### Set working location
```bash
gog calendar working-location --from="2026-02-10" --to="2026-02-10" --type=home
gog calendar working-location --from="2026-02-10" --to="2026-02-10" --type=office --office-label="NYC HQ"
```
Alias: `gog calendar wl`. Types: `home`, `office`, `custom`.

### Show calendar colors
```bash
gog calendar colors
```

### Check server time
```bash
gog calendar time
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--account=EMAIL` | Specify account for multi-account setups |
| `--json` | Output JSON (best for scripting/parsing) |
| `--plain` | Output stable TSV (no colors) |
| `--force` | Skip confirmations for destructive commands |
| `--no-input` | Never prompt; fail instead |

## Accounts

| Account | Owner | Default |
|---------|-------|---------|
| dylanbochman@gmail.com | Dylan | Yes (no `--account` needed) |
| julia.joy.jennings@gmail.com | Julia | Use `--account=julia.joy.jennings@gmail.com` |

When Dylan asks about "my calendar", use his account (default). When he mentions "Julia's calendar" or asks about her schedule, use `--account=julia.joy.jennings@gmail.com`.

### Examples
```bash
# Dylan's events (default)
gog calendar events --today

# Julia's events
gog calendar events --today --account=julia.joy.jennings@gmail.com

# Create event on Julia's calendar
gog calendar create primary \
  --summary="Dinner reservation" \
  --from="2026-02-14T19:00:00-05:00" \
  --to="2026-02-14T21:00:00-05:00" \
  --location="Restaurant" \
  --account=julia.joy.jennings@gmail.com

# Create event on both calendars (invite each other)
gog calendar create primary \
  --summary="Date night" \
  --from="2026-02-14T19:00:00-05:00" \
  --to="2026-02-14T21:00:00-05:00" \
  --attendees="julia.joy.jennings@gmail.com" \
  --send-updates=all

# Check both calendars for conflicts
gog calendar freebusy "dylanbochman@gmail.com,julia.joy.jennings@gmail.com" --from=... --to=...
```

## Notes

- Default account: dylanbochman@gmail.com
- **Always confirm with Dylan before creating, updating, or deleting events** — summarize what will be created (title, time, attendees) and wait for approval
- When creating events on Julia's calendar, double-confirm since it affects her schedule
- When showing upcoming events, default to today + next 7 days
- `gog` uses OAuth2 -- tokens refresh automatically
- Calendar ID `primary` refers to the default calendar
- Use `--json` when you need to parse event IDs for update/delete operations
- Times should be RFC3339 format with timezone (e.g., `2026-02-10T09:00:00-05:00`)
- Relative time values (`today`, `tomorrow`, `monday`) are supported in `--from`/`--to` for events, search, and conflicts
- Use `--all` with events to fetch across all calendars
- Always use `--force` and `--no-input` for non-interactive operation
- When the user says "weekly", "every Tuesday", "biweekly", "monthly", "recurring", "repeating", etc. → use `--rrule`
- Always confirm the recurrence pattern back to the user before creating (e.g., "I'll create a weekly event every Monday at 9am — sound good?")
- Default to no end date (`UNTIL`/`COUNT`) unless the user specifies one
- When modifying or deleting a recurring event, ask whether they mean this instance only, this and future instances, or the entire series
