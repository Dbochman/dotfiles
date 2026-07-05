---
name: gws-tasks
description: Read and manage Google Tasks task lists and tasks through the gws CLI. Use when the user asks to list, create, complete, move, update, clear, or delete Google Tasks or task lists; do not use for unrelated project-management systems.
---

# Google Tasks

Read the [Tasks gws reference](references/gws-cli.md) before selecting an account or executing a write.

## Discover the exact method

```bash
gws tasks --help
gws schema tasks.<resource>.<method>
```

Use `tasklists` for task-list operations and `tasks` for individual tasks. Build `--params` and `--json` from the installed schema rather than guessing fields.

Read-only examples:

```bash
gws tasks tasklists list
gws tasks tasks list --params '{"tasklist":"<TASKLIST_ID>","showCompleted":false}'
```

Write examples:

```bash
gws tasks tasks insert \
  --params '{"tasklist":"<TASKLIST_ID>"}' \
  --json '{"title":"<TITLE>","notes":"<NOTES>","due":"<RFC3339_DATE>"}'

gws tasks tasks patch \
  --params '{"tasklist":"<TASKLIST_ID>","task":"<TASK_ID>"}' \
  --json '{"status":"completed"}'
```

Google Tasks stores only the date portion of `due`; do not promise a due-time alarm.

## Required write gate

1. Resolve the account, task list, exact task IDs, and proposed changes with read-only calls.
2. Inspect the installed method schema and run the exact write with `--dry-run`.
3. Show the target and complete change set, then ask for explicit confirmation immediately before the real write.
4. Re-read the affected task or list and verify the requested postcondition.

Treat `tasks clear`, `tasks delete`, and `tasklists delete` as destructive. Before confirmation, state the number and identity of affected items. Deleting an assigned task or a task list containing assigned tasks can also delete the original assignment in Docs or Chat; do not perform it without the user's specific acknowledgement.
