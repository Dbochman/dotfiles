---
name: reminders
description: Manage Apple Reminders — create, view, complete, edit, and delete reminders and lists. Use when asked to set a reminder, check what's due, mark tasks done, manage to-do lists, or anything involving Apple Reminders.
allowed-tools: Bash(remindctl-wrapper:*)
metadata: {"openclaw":{"emoji":"✅","requires":{"bins":["remindctl-wrapper"]}}}
---

# Apple Reminders

Manage Apple Reminders via the `remindctl-wrapper` CLI.

## View Reminders

```bash
# Today's reminders
remindctl-wrapper today

# Other time ranges
remindctl-wrapper tomorrow
remindctl-wrapper week
remindctl-wrapper overdue
remindctl-wrapper upcoming
remindctl-wrapper all

# Specific date
remindctl-wrapper 2026-03-15

# JSON output for structured data
remindctl-wrapper today --json
```

## Add a Reminder

```bash
# Simple
remindctl-wrapper add "Buy groceries"

# With due date
remindctl-wrapper add "Call dentist" --due tomorrow
remindctl-wrapper add "Pay rent" --due 2026-03-01

# With list and priority
remindctl-wrapper add "Finish report" --list Work --due tomorrow --priority high

# JSON output to get the created reminder ID
remindctl-wrapper add "Pick up package" --due today --json
```

## Complete Reminders

```bash
# By ID (prefix match)
remindctl-wrapper complete 3DD2

# Multiple at once
remindctl-wrapper complete 3DD2 A1B2 C3D4
```

## Edit Reminders

```bash
remindctl-wrapper edit 3DD2 --title "Updated title"
remindctl-wrapper edit 3DD2 --due 2026-03-10
remindctl-wrapper edit 3DD2 --priority medium
```

## Delete Reminders

```bash
remindctl-wrapper delete 3DD2 --force
```

## Manage Lists

```bash
# View all lists
remindctl-wrapper list

# Show reminders in a specific list
remindctl-wrapper list Work

# Create a new list
remindctl-wrapper list Shopping --create

# Rename a list
remindctl-wrapper list Work --rename Office

# Delete a list
remindctl-wrapper list Old --delete
```

## Output Formats

- Default: human-readable table
- `--json`: structured JSON (best for parsing)
- `--plain`: tab-separated values
- `--quiet`: count summaries only

## Notes

- Binary at `/opt/homebrew/bin/remindctl-wrapper` (wraps `/opt/homebrew/bin/remindctl`)
- Wrapper runs commands via GUI Terminal session (EventKit TCC requires it)
- Reminders sync via iCloud across all Apple devices
- IDs are UUIDs; prefix matching works (use first 4+ chars)
- Due dates: `today`, `tomorrow`, `YYYY-MM-DD`, ISO 8601
- Priority: `none`, `low`, `medium`, `high`
