---
name: gmail
description: Read, search, send, and manage Gmail messages. Use when asked about email, inbox, unread messages, sending emails, or checking mail.
allowed-tools: Bash(gmail:*)
metadata: {"openclaw":{"emoji":"E","requires":{"bins":["gog"]}}}
---

# Gmail

Access Gmail via the `gog` CLI (v0.9.0). Credentials are managed via file-based OAuth2 keyring.

## Reading Email

### Search threads (default view)
```bash
gog gmail search "is:unread"
gog gmail search "from:someone@example.com"
gog gmail search "subject:invoice after:2026/01/01"
gog gmail search "has:attachment filename:pdf"
gog gmail search "is:unread" --max=20
gog gmail search "label:important" --json
```
Uses standard Gmail query syntax. Returns threads (grouped conversations). Default max is 10.

### Search individual messages
```bash
gog gmail messages search "is:unread" --max=5
gog gmail messages search "from:boss@company.com" --include-body
```
Returns individual messages instead of threads. Use `--include-body` to see message content inline.

### Get a single message
```bash
gog gmail get <messageId>
gog gmail get <messageId> --format=metadata --headers=From,Subject,Date
gog gmail get <messageId> --format=raw
gog gmail get <messageId> --json
```
Formats: `full` (default), `metadata` (headers only), `raw` (RFC 2822).

### Get a full thread
```bash
gog gmail thread get <threadId>
gog gmail thread get <threadId> --full
gog gmail thread get <threadId> --download --out-dir=./attachments
```
Use `--full` to show complete message bodies. Use `--download` to save attachments.

### Download an attachment
```bash
gog gmail attachment <messageId> <attachmentId>
gog gmail attachment <messageId> <attachmentId> --out=/tmp/file.pdf
```

### List thread attachments
```bash
gog gmail thread attachments <threadId>
```

### Get Gmail web URL for threads
```bash
gog gmail url <threadId>
```

### View history
```bash
gog gmail history --since=<historyId> --max=100
```

## Sending Email

### Send a message
```bash
gog gmail send --to="recipient@example.com" --subject="Hello" --body="Message body"
gog gmail send --to="a@x.com,b@x.com" --cc="c@x.com" --bcc="d@x.com" --subject="Team update" --body="Details here"
gog gmail send --to="recipient@example.com" --subject="Report" --body="See attached" --attach=report.pdf
gog gmail send --to="recipient@example.com" --subject="HTML email" --body-html="<h1>Hello</h1>"
gog gmail send --body-file=message.txt --to="recipient@example.com" --subject="From file"
```

### Reply to a message
```bash
gog gmail send --reply-to-message-id=<messageId> --body="Thanks for the update"
gog gmail send --thread-id=<threadId> --reply-all --body="Acknowledged"
```
Use `--reply-all` with `--reply-to-message-id` or `--thread-id` to auto-populate recipients.

### Open tracking
```bash
gog gmail send --to="recipient@example.com" --subject="Proposal" --body-html="<p>Details</p>" --track
gog gmail track opens
gog gmail track opens <tracking-id>
gog gmail track status
gog gmail track setup
```

## Drafts

```bash
gog gmail drafts list
gog gmail drafts list --max=20
gog gmail drafts get <draftId>
gog gmail drafts create --to="recipient@example.com" --subject="Draft" --body="WIP"
gog gmail drafts create --reply-to-message-id=<messageId> --body="Draft reply"
gog gmail drafts update <draftId> --body="Updated content"
gog gmail drafts send <draftId>
gog gmail drafts delete <draftId>
```

## Labels & Organization

### Manage labels
```bash
gog gmail labels list
gog gmail labels get <labelNameOrId>
gog gmail labels create "My New Label"
```

### Apply/remove labels on threads
```bash
gog gmail labels modify <threadId> --add="STARRED" --remove="UNREAD"
gog gmail labels modify <threadId1> <threadId2> --add="Important"
gog gmail thread modify <threadId> --add="STARRED" --remove="INBOX"
```

### Batch operations on messages
```bash
gog gmail batch modify <msgId1> <msgId2> --add="READ" --remove="UNREAD"
gog gmail batch delete <msgId1> <msgId2>
```
**Warning:** `batch delete` permanently deletes messages. Use `--force` to skip confirmation.

## Settings & Admin

```bash
gog gmail settings filters list
gog gmail settings delegates list
gog gmail settings forwarding list
gog gmail settings autoforward get
gog gmail settings sendas list
gog gmail settings vacation get
gog gmail settings watch start
```

## Common Gmail Query Syntax

| Query | Meaning |
|-------|---------|
| `is:unread` | Unread messages |
| `is:starred` | Starred messages |
| `is:important` | Important messages |
| `in:inbox` | Messages in inbox |
| `in:sent` | Sent messages |
| `in:trash` | Trashed messages |
| `from:user@example.com` | From specific sender |
| `to:user@example.com` | To specific recipient |
| `subject:keyword` | Subject contains keyword |
| `has:attachment` | Has attachments |
| `filename:pdf` | Has PDF attachment |
| `after:2026/01/01` | After date |
| `before:2026/02/01` | Before date |
| `newer_than:7d` | Newer than 7 days |
| `older_than:1m` | Older than 1 month |
| `label:work` | Has label "work" |
| `category:promotions` | In promotions category |

Combine queries: `from:boss@co.com is:unread after:2026/01/01`

## Global Flags

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON (best for scripting) |
| `--plain` | Output as TSV (parseable, no colors) |
| `--account=EMAIL` | Use specific account |
| `--force` | Skip confirmations for destructive commands |
| `--no-input` | Never prompt; fail instead (CI mode) |
| `--verbose` | Enable verbose logging |
| `-z, --timezone=IANA` | Set output timezone (e.g. America/New_York) |

## Notes

- Account: dylanbochman@gmail.com
- Always check inbox/unread first before reporting on emails
- When sending emails, confirm the recipient and content with the user first
- gog uses OAuth2 -- tokens refresh automatically
- Use `--json` when you need to parse output programmatically or extract IDs
- Thread search (`gog gmail search`) groups messages into conversations; message search (`gog gmail messages search`) returns individual messages
- The `--plain` flag outputs TSV format, useful for piping to other tools
