---
name: gws-gmail
description: Read, search, send, and manage Gmail messages for Dylan, Julia, or other accounts. Use when asked about email, inbox, unread messages, sending emails, checking mail, or Julia's email.
allowed-tools: Bash(gmail:*)
metadata: {"openclaw":{"emoji":"E","requires":{"bins":["gws"]}}}
---

# Gmail (gws)

Access Gmail via the `gws` CLI at `/opt/homebrew/bin/gws`. Credentials are AES-256-GCM encrypted at `~/.config/gws/`.

## Accounts

| Account | Owner | Raw Gmail API selector |
|---------|-------|-------------------------|
| dylanbochman@gmail.com | Dylan | Default, or `GOOGLE_WORKSPACE_CLI_ACCOUNT=dylanbochman@gmail.com` |
| julia.joy.jennings@gmail.com | Julia | `GOOGLE_WORKSPACE_CLI_ACCOUNT=julia.joy.jennings@gmail.com` |
| bochmanspam@gmail.com | Dylan (spam) | `GOOGLE_WORKSPACE_CLI_ACCOUNT=bochmanspam@gmail.com` |
| clawdbotbochman@gmail.com | OpenClaw | `GOOGLE_WORKSPACE_CLI_ACCOUNT=clawdbotbochman@gmail.com` |

When Dylan asks about "my email", use default. When he says "Julia's email", use her account.

## Command Pattern

Raw Gmail API commands follow:

`GOOGLE_WORKSPACE_CLI_ACCOUNT=<email> gws gmail <resource> <method> [--params '<JSON>'] [--json '<JSON>']`

- `--params` = URL/query parameters (userId, q, maxResults, etc.)
- `--json` = request body (for send, modify, etc.)

With pinned GWS 0.4.4, do not use `--account` on raw API resource commands;
that flag is not reliably routed and can return `No credentials provided`.
Use `GOOGLE_WORKSPACE_CLI_ACCOUNT` instead. Reserve `--account` for helper or
authentication subcommands that explicitly support it.

**Important:** Most Gmail endpoints require `"userId": "me"` in params.

## Search Messages

```bash
# Unread messages
gws gmail users messages list --params '{
  "userId": "me",
  "q": "is:unread",
  "maxResults": 10
}'

# From a specific sender
gws gmail users messages list --params '{
  "userId": "me",
  "q": "from:someone@example.com"
}'

# With attachments, recent
gws gmail users messages list --params '{
  "userId": "me",
  "q": "has:attachment newer_than:7d"
}'

# Julia's unread
GOOGLE_WORKSPACE_CLI_ACCOUNT=julia.joy.jennings@gmail.com gws gmail users messages list --params '{
  "userId": "me",
  "q": "is:unread",
  "maxResults": 10
}'
```

### Common Gmail Query Syntax

| Query | Meaning |
|-------|---------|
| `is:unread` | Unread messages |
| `is:starred` | Starred messages |
| `is:important` | Important messages |
| `in:inbox` | Messages in inbox |
| `in:sent` | Sent messages |
| `from:user@example.com` | From specific sender |
| `to:user@example.com` | To specific recipient |
| `subject:keyword` | Subject contains keyword |
| `has:attachment` | Has attachments |
| `filename:pdf` | Has PDF attachment |
| `after:2026/01/01` | After date |
| `before:2026/02/01` | Before date |
| `newer_than:7d` | Newer than 7 days |
| `older_than:1m` | Older than 1 month |
| `label:work` | Has label |
| `category:promotions` | In category |

Combine queries: `from:boss@co.com is:unread after:2026/01/01`

## Get a Single Message

```bash
# Full message with body
gws gmail users messages get --params '{
  "userId": "me",
  "id": "<messageId>",
  "format": "full"
}'

# Metadata only (headers)
gws gmail users messages get --params '{
  "userId": "me",
  "id": "<messageId>",
  "format": "metadata"
}'
```

With pinned GWS 0.4.4, passing `metadataHeaders` through `--params` can return
`payload` without a `headers` array. Omit that parameter, request metadata, and
filter the returned headers locally. Do not pipe an API error object into `jq`;
check the command status and `.error` first.

### Response shape — where headers actually live

The `metadata` response does NOT put headers at the top level. They're nested under `payload.headers` as an array of `{name, value}` objects:

```json
{
  "id": "...",
  "internalDate": "1777719291000",
  "labelIds": ["INBOX", ...],
  "payload": {
    "headers": [
      {"name": "From", "value": "..."},
      {"name": "Subject", "value": "..."},
      {"name": "Date", "value": "..."}
    ]
  }
}
```

To extract them flat, pipe through jq:

```bash
if ! out=$(gws gmail users messages get --params '{"userId":"me","id":"<id>","format":"metadata"}' 2>&1); then
  echo "Gmail metadata unavailable" >&2
  exit 1
fi
jq -e '(.error? == null) and (.payload.headers | type == "array")' <<<"$out" >/dev/null || {
  echo "Gmail metadata unavailable" >&2
  exit 1
}
jq '.payload.headers | map(select(.name == "From" or .name == "Subject" or .name == "Date") | {(.name): .value}) | add' <<<"$out"
# → {"From":"sender@x.com","Subject":"...","Date":"..."}
```

For batch summaries (e.g., morning briefing), loop a list call's IDs through this pattern and concat — don't fall back to `format: "full"` snippets, which is slow and lossy.

## Get a Thread

```bash
gws gmail users threads get --params '{
  "userId": "me",
  "id": "<threadId>",
  "format": "full"
}'
```

## Send a Message

Messages must be base64url-encoded RFC 2822 format.

**IMPORTANT:** Use Python `base64.urlsafe_b64encode` to encode the raw email. Do NOT use shell `printf | base64 | tr` — it corrupts `!` to `\!` and mangles special characters.

```bash
# Step 1: Build base64url payload with Python (safe for all characters)
RAW_B64=$(python3 -c "
import base64
msg = 'From: sender@gmail.com\r\nTo: recipient@example.com\r\nSubject: Hello\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nMessage body here!'
print(base64.urlsafe_b64encode(msg.encode()).decode().rstrip('='))
")

# Step 2: Send via gws
gws gmail users messages send --params '{"userId": "me"}' \
  --json "{\"raw\":\"${RAW_B64}\"}"

# Reply to a thread (add threadId, In-Reply-To, References headers)
RAW_B64=$(python3 -c "
import base64
msg = 'From: sender@gmail.com\r\nTo: recipient@example.com\r\nSubject: Re: Original Subject\r\nIn-Reply-To: <original-message-id>\r\nReferences: <original-message-id>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nReply body here'
print(base64.urlsafe_b64encode(msg.encode()).decode().rstrip('='))
")
gws gmail users messages send --params '{"userId": "me"}' \
  --json "{\"threadId\":\"<threadId>\",\"raw\":\"${RAW_B64}\"}"
```

For HTML emails, use `Content-Type: text/html; charset=utf-8` in the headers.

## Drafts

```bash
# List drafts
gws gmail users drafts list --params '{"userId": "me"}'

# Create a draft (use Python base64 pattern from Send section)
RAW_B64=$(python3 -c "
import base64
msg = 'To: recipient@example.com\r\nSubject: Draft\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nDraft body'
print(base64.urlsafe_b64encode(msg.encode()).decode().rstrip('='))
")
gws gmail users drafts create --params '{"userId": "me"}' \
  --json "{\"message\":{\"raw\":\"${RAW_B64}\"}}"

# Send a draft
gws gmail users drafts send --params '{"userId": "me"}' --json '{
  "id": "<draftId>"
}'

# Delete a draft
gws gmail users drafts delete --params '{"userId": "me", "id": "<draftId>"}'
```

## Labels

```bash
# List all labels
gws gmail users labels list --params '{"userId": "me"}'

# Create a label
gws gmail users labels create --params '{"userId": "me"}' --json '{
  "name": "OpenClaw/NewLabel",
  "labelListVisibility": "labelShow",
  "messageListVisibility": "show"
}'

# Get label details
gws gmail users labels get --params '{"userId": "me", "id": "<labelId>"}'
```

## Modify Message Labels

```bash
# Mark as read (remove UNREAD)
gws gmail users messages modify --params '{"userId": "me", "id": "<messageId>"}' --json '{
  "removeLabelIds": ["UNREAD"]
}'

# Star a message
gws gmail users messages modify --params '{"userId": "me", "id": "<messageId>"}' --json '{
  "addLabelIds": ["STARRED"]
}'

# Archive (remove from INBOX)
gws gmail users messages modify --params '{"userId": "me", "id": "<messageId>"}' --json '{
  "removeLabelIds": ["INBOX"]
}'

# Apply custom label
gws gmail users messages modify --params '{"userId": "me", "id": "<messageId>"}' --json '{
  "addLabelIds": ["<labelId>"]
}'
```

## Batch Modify

```bash
gws gmail users messages batchModify --params '{"userId": "me"}' --json '{
  "ids": ["<msgId1>", "<msgId2>"],
  "addLabelIds": ["STARRED"],
  "removeLabelIds": ["UNREAD"]
}'
```

## Trash / Delete

```bash
# Trash (recoverable)
gws gmail users messages trash --params '{"userId": "me", "id": "<messageId>"}'

# Permanent delete (irreversible!)
gws gmail users messages delete --params '{"userId": "me", "id": "<messageId>"}'
```

## Thread Operations

```bash
# List threads
gws gmail users threads list --params '{
  "userId": "me",
  "q": "is:unread",
  "maxResults": 10
}'

# Modify thread labels
gws gmail users threads modify --params '{"userId": "me", "id": "<threadId>"}' --json '{
  "addLabelIds": ["STARRED"],
  "removeLabelIds": ["UNREAD"]
}'

# Trash entire thread
gws gmail users threads trash --params '{"userId": "me", "id": "<threadId>"}'
```

## Attachments

```bash
# Get attachment (returns base64 data)
gws gmail users messages attachments get --params '{
  "userId": "me",
  "messageId": "<messageId>",
  "id": "<attachmentId>"
}'
```

## Settings

```bash
# Vacation responder
gws gmail users settings getVacation --params '{"userId": "me"}'

# Filters
gws gmail users settings filters list --params '{"userId": "me"}'

# Forwarding
gws gmail users settings forwardingAddresses list --params '{"userId": "me"}'

# Send-as aliases
gws gmail users settings sendAs list --params '{"userId": "me"}'
```

## API Schema

For any endpoint, check available parameters:
```bash
gws schema gmail.users.messages.list
gws schema gmail.users.messages.send
gws schema gmail.users.threads.get
```

## Automated Inbox Management (Julia)

Julia's inbox automation is split into two daily cron jobs:

- **Silent Triage** (6:45 AM ET): categorizes and labels mail, creates guarded reply drafts, resolves read state, archives stale read mail, and conservatively trashes clear spam; produces a JSON handoff without delivering a message.
- **Read-Only Briefing** (7 AM ET): combines that handoff with Calendar and household summaries, then delivers the concise briefing to Julia via iMessage without mutating Gmail.

### OpenClaw Labels (Julia's account)

| Label | Purpose |
|-------|---------|
| `OpenClaw/Urgent` | Time-sensitive, needs immediate attention |
| `OpenClaw/Action` | Requires response or action from Julia |
| `OpenClaw/FYI` | Informational, no action needed |
| `OpenClaw/Financial` | Bills, bank alerts, transactions |
| `OpenClaw/Shopping` | Orders, shipping, receipts |
| `OpenClaw/Newsletters` | Subscriptions, digests, promotions |
| `OpenClaw/Social` | Social media notifications, invites |

## CRITICAL: Sending Emails

**NEVER send an email unless the user has explicitly asked you to send it.** Drafting an email is not permission to send it. Approving a draft is not permission to send it. The user must clearly and unambiguously instruct you to send before you call the send endpoint. When in doubt, ask. This applies to all accounts and all recipients — no exceptions.

## Notes

- **Only use the `gws` CLI for Gmail** — do NOT use `himalaya`, `mutt`, `mail`, or any other email CLI. The `gws` CLI handles multi-account auth and is the only supported tool.
- Default account: dylanbochman@gmail.com
- Always check inbox/unread first before reporting on emails
- `gws` outputs JSON by default — parse directly or pipe through `jq`
- Thread endpoints group messages into conversations; message endpoints return individual messages
- The `raw` field for send/drafts uses base64url encoding — always use Python `base64.urlsafe_b64encode` (shell `printf | base64 | tr` corrupts `!` and other special chars)
- With GWS 0.4.4, use `format: "metadata"` without `metadataHeaders`, validate the response, and select only the needed headers locally
