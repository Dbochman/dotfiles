---
name: gmail
description: Read, search, send, and manage Gmail messages for Dylan or Julia. Use when asked about email, inbox, unread messages, sending emails, checking mail, or Julia's email.
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

## Accounts

| Account | Owner | Default |
|---------|-------|---------|
| dylanbochman@gmail.com | Dylan | Yes (no `--account` needed) |
| julia.joy.jennings@gmail.com | Julia | Use `--account=julia.joy.jennings@gmail.com` |

When Dylan asks about "my email", use his account (default). When he mentions "Julia's email" or asks to check/send on her behalf, use `--account=julia.joy.jennings@gmail.com`.

### Examples
```bash
# Dylan's inbox (default)
gog gmail search "is:unread"

# Julia's inbox
gog gmail search "is:unread" --account=julia.joy.jennings@gmail.com

# Send from Julia's account
gog gmail send --to="someone@example.com" --subject="Hi" --body="..." --account=julia.joy.jennings@gmail.com
```

## Automated Inbox Management (Julia)

Julia's inbox has automated daily triage via two cron jobs:

- **Morning Triage** (7 AM ET): Searches unread inbox, categorizes with labels, creates draft replies, sends summary via iMessage
- **Evening Cleanup** (8 PM ET): Archives old read emails, identifies unsubscribe candidates, cleans spam, sends digest via iMessage

### OpenClaw Labels

These labels are created and managed by the automated triage system on Julia's account:

| Label | Purpose |
|-------|---------|
| `OpenClaw/Urgent` | Time-sensitive, needs immediate attention |
| `OpenClaw/Action` | Requires response or action from Julia |
| `OpenClaw/FYI` | Informational, no action needed |
| `OpenClaw/Financial` | Bills, bank alerts, transactions |
| `OpenClaw/Shopping` | Orders, shipping, receipts |
| `OpenClaw/Newsletters` | Subscriptions, digests, promotions |
| `OpenClaw/Social` | Social media notifications, invites |

When working with Julia's inbox manually, be aware these labels exist and respect the triage system's categorization.

## Auth Troubleshooting

gog uses file-based OAuth2 with an encrypted keyring at `~/Library/Application Support/gogcli/keyring`. The keyring passphrase is stored in 1Password at `op://OpenClaw/GOG CLI/password` and injected via `GOG_KEYRING_PASSWORD` env var by the OpenClaw gateway startup script.

### Common failures

| Error | Cause | Fix |
|-------|-------|-----|
| `aes.KeyUnwrap(): integrity check failed` | Keyring passphrase mismatch or corrupted token | Re-auth with correct 1Password passphrase |
| `oauth2: "invalid_grant" "Token has been expired or revoked."` | Google revoked the refresh token | Re-auth: `gog auth add <email>` |
| `no TTY available for keyring file backend password prompt` | Non-interactive session missing `GOG_KEYRING_PASSWORD` | Set env var from 1Password or cache |

### Re-authentication steps

1. VNC into Mac Mini (`vnc://100.93.66.71` via Tailscale)
2. Open Terminal and run: `gog auth add julia.joy.jennings@gmail.com`
3. Complete Google OAuth flow in the browser
4. When prompted for keyring passphrase, use the value from: `/opt/homebrew/bin/op read "op://OpenClaw/GOG CLI/password"`
5. Verify: `GOG_KEYRING_PASSWORD=$(cat ~/.cache/openclaw-gateway/gog_keyring_password) gog gmail search "is:unread" --account=julia.joy.jennings@gmail.com --max=1`

### Cron job error reporting

Both Gmail cron jobs have a Step 0 auth health check. If auth fails, they stop immediately and send an error message via iMessage to Julia with re-auth instructions.

## Cron Job Sync

Job definitions are tracked in the dotfiles repo (state-stripped) and synced to the Mac Mini:

```bash
# Save live jobs to dotfiles (strips runtime state)
~/dotfiles/openclaw/sync-cron-jobs.sh save

# Deploy definitions to live file (preserves runtime state)
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

Deploy runs automatically on the Mac Mini after every `dotfiles-pull`.

## Notes

- Default account: dylanbochman@gmail.com
- Always check inbox/unread first before reporting on emails
- When sending emails, confirm the recipient and content with the user first
- gog uses OAuth2 -- tokens refresh automatically but can expire if the Google Cloud OAuth app was in "Testing" mode (7-day token lifespan). The app has been upgraded to Production for longer-lived tokens.
- Use `--json` when you need to parse output programmatically or extract IDs
- Thread search (`gog gmail search`) groups messages into conversations; message search (`gog gmail messages search`) returns individual messages
- The `--plain` flag outputs TSV format, useful for piping to other tools
