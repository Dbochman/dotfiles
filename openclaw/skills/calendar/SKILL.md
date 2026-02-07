---
name: calendar
description: View, create, and manage Google Calendar events. Use when asked about schedule, appointments, meetings, calendar, what's coming up, free time, conflicts, or availability.
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

### Update an event
```bash
gog calendar update primary <eventId> \
  --summary="New Title" \
  --from="2026-02-10T10:00:00-05:00" \
  --to="2026-02-10T10:30:00-05:00"
```
Set any field to empty string to clear it. For recurring events use `--scope=<single|future|all>` with `--original-start`. Supports `--add-attendee` to add without replacing.

### Delete an event
```bash
gog calendar delete primary <eventId>
```
For recurring events: `--scope=<single|future|all>` with `--original-start`.

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

## Notes

- Account: dylanbochman@gmail.com
- When showing upcoming events, default to today + next 7 days
- `gog` uses OAuth2 -- tokens refresh automatically
- Calendar ID `primary` refers to the default calendar
- Use `--json` when you need to parse event IDs for update/delete operations
- Times should be RFC3339 format with timezone (e.g., `2026-02-10T09:00:00-05:00`)
- Relative time values (`today`, `tomorrow`, `monday`) are supported in `--from`/`--to` for events, search, and conflicts
- Use `--all` with events to fetch across all calendars
- Always use `--force` and `--no-input` for non-interactive operation
