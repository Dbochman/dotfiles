---
name: recipe-create-vacation-responder
description: Configure or disable Gmail's vacation responder with an exact subject, message, audience, and optional date range. Use when the user asks to enable, change, inspect, or turn off an out-of-office automatic reply.
---

# Configure a Gmail vacation responder

Use [gws-gmail](../gws-gmail/SKILL.md) for account selection and Gmail API details.

## Workflow

1. Read the current settings without changing them:

   ```bash
   gws gmail users settings getVacation --params '{"userId":"me"}'
   ```

2. Resolve the exact subject, plain-text body, audience restrictions, start, end, timezone, and target account. Convert start/end to epoch milliseconds; verify that start precedes end. Omit date fields only when the user intentionally wants the responder enabled without an automatic window.
3. Build the update using placeholders rather than copying stale dates:

   ```bash
   gws gmail users settings updateVacation \
     --params '{"userId":"me"}' \
     --json '{"enableAutoReply":true,"responseSubject":"<SUBJECT>","responseBodyPlainText":"<BODY>","restrictToContacts":true,"restrictToDomain":false,"startTime":"<START_EPOCH_MS>","endTime":"<END_EPOCH_MS>"}'
   ```

4. Run the exact update with `--dry-run`. Show the user the account, complete subject/body, human-readable local start/end, timezone, and recipient restrictions.
5. Ask for explicit confirmation immediately before running the update without `--dry-run`.
6. Read the settings back with `getVacation` and verify every confirmed field.

Disabling is also a write and requires its own explicit confirmation:

```bash
gws gmail users settings updateVacation \
  --params '{"userId":"me"}' \
  --json '{"enableAutoReply":false}'
```

Do not assume a prior planned return date is approval to disable the responder later.
