---
name: gws-calendar-insert
description: Create one simple, non-recurring Google Calendar event with the gws helper. Use when the user asks to add an event with an explicit title and start/end time; use gws-calendar for recurring events, conference links, updates, or deletions.
---

# Insert a Google Calendar event

Use `gws calendar +insert` for a simple one-time event. Read the [Calendar gws reference](../gws-calendar/references/gws-cli.md) before choosing an account or executing the write.

## Command shape

```bash
gws calendar +insert \
  --summary '<TITLE>' \
  --start '<RFC3339_START>' \
  --end '<RFC3339_END>' \
  [--calendar '<CALENDAR_ID>'] \
  [--location '<LOCATION>'] \
  [--description '<DESCRIPTION>'] \
  [--attendee '<EMAIL>'] [--attendee '<EMAIL>']
```

Use RFC3339 timestamps with explicit offsets, such as `2026-07-08T09:00:00-04:00`. Compute the current offset for the event's timezone; do not copy a fixed example offset across daylight-saving boundaries. The `--attendee` flag is singular and repeatable.

## Required write gate

1. Resolve the target account and calendar, title, start, end, timezone, location, description, and complete attendee list. Ask about any material ambiguity.
2. Run the exact command with `--dry-run` to validate it locally.
3. Show the user the resolved account, calendar, local date/time and timezone, title, location, and attendees. Mention that attendees may receive invitations.
4. Ask for explicit confirmation immediately before running the command without `--dry-run`.
5. After execution, verify the returned event ID, title, and start/end. Report failure instead of assuming creation succeeded.

Do not create an event from a tentative suggestion. Use [gws-calendar](../gws-calendar/SKILL.md) for recurrence, Google Meet, or other raw API features.
