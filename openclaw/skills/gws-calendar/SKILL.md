---
name: gws-calendar
description: View, create, and manage Google Calendar events for Dylan, Julia, or other accounts. Use when asked about schedule, appointments, meetings, calendar, what's coming up, free time, conflicts, or availability.
allowed-tools: Bash(calendar:*)
metadata: {"openclaw":{"emoji":"📅","requires":{"bins":["gws"]}}}
---

# Google Calendar (gws)

Access Google Calendar via the `gws` CLI at `/opt/homebrew/bin/gws`. Credentials are AES-256-GCM encrypted at `~/.config/gws/`.

## Accounts

| Account | Owner | Flag |
|---------|-------|------|
| dylanbochman@gmail.com | Dylan | Default (no flag needed) |
| julia.joy.jennings@gmail.com | Julia | `--account julia.joy.jennings@gmail.com` |
| bochmanspam@gmail.com | Dylan (spam) | `--account bochmanspam@gmail.com` |
| clawdbotbochman@gmail.com | OpenClaw | `--account clawdbotbochman@gmail.com` |

When Dylan asks about "my calendar", use default. When he says "Julia's calendar", use her account.

## Command Pattern

All commands follow: `gws calendar <resource> <method> [--params '<JSON>'] [--json '<JSON>'] [--account <email>]`

- `--params` = URL/query parameters (calendarId, timeMin, maxResults, etc.)
- `--json` = request body (for create/update)
- `--account` = target account (omit for Dylan)

## List Calendars

```bash
gws calendar calendarList list
```

## List Events

```bash
# Today's events
gws calendar events list --params '{
  "calendarId": "primary",
  "timeMin": "2026-03-05T00:00:00-05:00",
  "timeMax": "2026-03-06T00:00:00-05:00",
  "singleEvents": true,
  "orderBy": "startTime"
}'

# Next 7 days
gws calendar events list --params '{
  "calendarId": "primary",
  "timeMin": "2026-03-05T00:00:00-05:00",
  "timeMax": "2026-03-12T00:00:00-05:00",
  "singleEvents": true,
  "orderBy": "startTime"
}'

# Julia's events today
gws calendar events list --params '{
  "calendarId": "primary",
  "timeMin": "2026-03-05T00:00:00-05:00",
  "timeMax": "2026-03-06T00:00:00-05:00",
  "singleEvents": true,
  "orderBy": "startTime"
}' --account julia.joy.jennings@gmail.com

# All calendars (use calendarList first, then query each)
gws calendar events list --params '{
  "calendarId": "<calendarId>",
  "timeMin": "...",
  "timeMax": "...",
  "singleEvents": true,
  "orderBy": "startTime"
}'
```

**Important:** Always compute `timeMin`/`timeMax` as RFC3339 timestamps with timezone. Use `America/New_York` (-05:00 EST / -04:00 EDT).

## Get a Single Event

```bash
gws calendar events get --params '{
  "calendarId": "primary",
  "eventId": "<eventId>"
}'
```

## Search Events

Use the `q` parameter:
```bash
gws calendar events list --params '{
  "calendarId": "primary",
  "q": "dinner",
  "timeMin": "2026-03-01T00:00:00-05:00",
  "timeMax": "2026-03-31T00:00:00-05:00",
  "singleEvents": true,
  "orderBy": "startTime"
}'
```

## Create an Event

```bash
gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "Team Standup",
  "description": "Daily sync",
  "location": "Zoom",
  "start": {"dateTime": "2026-03-10T09:00:00-05:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-03-10T09:30:00-05:00", "timeZone": "America/New_York"},
  "attendees": [
    {"email": "alice@example.com"}
  ]
}'
```

### All-day event
```bash
gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "Vacation",
  "start": {"date": "2026-03-15"},
  "end": {"date": "2026-03-16"}
}'
```

### Recurring event
```bash
gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "Weekly Standup",
  "start": {"dateTime": "2026-03-10T09:00:00-05:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-03-10T09:15:00-05:00", "timeZone": "America/New_York"},
  "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]
}'
```

### Common RRULE patterns

| Pattern | RRULE |
|---------|-------|
| Every Monday | `RRULE:FREQ=WEEKLY;BYDAY=MO` |
| Every weekday | `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` |
| Biweekly Tuesday | `RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU` |
| Daily | `RRULE:FREQ=DAILY` |
| 1st of every month | `RRULE:FREQ=MONTHLY;BYMONTHDAY=1` |
| First Friday of every month | `RRULE:FREQ=MONTHLY;BYDAY=1FR` |
| Weekly until end of year | `RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231T235959Z` |

### With Google Meet
```bash
gws calendar events insert --params '{"calendarId": "primary", "conferenceDataVersion": 1}' --json '{
  "summary": "1:1 with Alice",
  "start": {"dateTime": "2026-03-10T14:00:00-05:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-03-10T14:30:00-05:00", "timeZone": "America/New_York"},
  "attendees": [{"email": "alice@example.com"}],
  "conferenceData": {
    "createRequest": {"requestId": "meet-1", "conferenceSolutionKey": {"type": "hangoutsMeet"}}
  }
}'
```

## Update an Event

```bash
gws calendar events patch --params '{
  "calendarId": "primary",
  "eventId": "<eventId>"
}' --json '{
  "summary": "New Title",
  "start": {"dateTime": "2026-03-10T10:00:00-05:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-03-10T10:30:00-05:00", "timeZone": "America/New_York"}
}'
```

Use `patch` (not `update`) to only change specified fields.

## Delete an Event

```bash
gws calendar events delete --params '{
  "calendarId": "primary",
  "eventId": "<eventId>"
}'
```

## Check Free/Busy

```bash
gws calendar freebusy query --json '{
  "timeMin": "2026-03-10T00:00:00-05:00",
  "timeMax": "2026-03-10T23:59:59-05:00",
  "items": [
    {"id": "dylanbochman@gmail.com"},
    {"id": "julia.joy.jennings@gmail.com"}
  ]
}'
```

## Respond to an Invitation

First get the event, then patch the attendee status:
```bash
# Get the event to find your attendee entry
gws calendar events get --params '{"calendarId": "primary", "eventId": "<eventId>"}'

# Accept/decline by updating the event
gws calendar events patch --params '{
  "calendarId": "primary",
  "eventId": "<eventId>"
}' --json '{
  "attendees": [{"email": "dylanbochman@gmail.com", "responseStatus": "accepted"}]
}'
```

## API Schema

For any command, check available parameters:
```bash
gws schema calendar.events.list
gws schema calendar.events.insert
```

## Notes

- Default account: dylanbochman@gmail.com
- **Always confirm with Dylan before creating, updating, or deleting events**
- When creating events on Julia's calendar, double-confirm since it affects her schedule
- Calendar ID `primary` = default calendar for the account
- Times must be RFC3339 with timezone (e.g., `2026-03-10T09:00:00-05:00`)
- Use `singleEvents: true` + `orderBy: startTime` when listing to expand recurring events
- When the user says "weekly", "recurring", etc. -> use `recurrence` array with RRULE
- Always confirm recurrence pattern before creating
- For recurring event modifications, use the instance eventId (contains `_` suffix) for single-instance changes
- `gws` outputs JSON by default — pipe through `jq` or parse directly
