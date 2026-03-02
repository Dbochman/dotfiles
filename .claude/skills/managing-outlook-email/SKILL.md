---
name: managing-outlook-email
description: Manage Microsoft Outlook email via CLI. Search, read, open, triage, archive, flag, and organize inbox messages. Use when working with email, checking unread messages, searching mail, or organizing inbox.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start, commands
- Level 3: workflows/ for detailed procedures

Related skills:
- managing-calendar: For calendar and scheduling
- reading-meeting-transcripts: For Teams meeting transcripts
-->

# Outlook Email Management

Manage Microsoft Outlook email via `outlook-cli` - search, read, triage, archive, and organize messages.

## Verify Installation

```bash
# Check tool is available
outlook-cli --version

# Test authentication (will prompt for login if needed)
outlook-cli find --only-unread --limit 1 --toon
```

If command not found, see [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/). For calendar operations, see [managing-calendar](../managing-calendar/SKILL.md). For meeting transcripts, see [reading-meeting-transcripts](../reading-meeting-transcripts/SKILL.md).

## When to Use This Skill

Use this skill when users want to:

- **Check inbox**: View unread emails, recent messages
- **Search email**: Find messages by sender, subject, date, content
- **Triage inbox**: Review and process unread messages
- **Organize mail**: Move to folders, archive old messages
- **Track follow-ups**: Flag emails, find flagged items
- **Bulk operations**: Archive by date, mark by sender

## Quick Start Examples

### Check Unread Emails
```bash
# List unread emails (compact output)
outlook-cli find --only-unread --toon

# Unread from specific sender
outlook-cli find --only-unread --from boss@example.com --toon

# Unread with specific subject
outlook-cli find --only-unread --subject "urgent" --toon
```

### Read Email Content
```bash
# Read full email (HTML converted to markdown by default)
outlook-cli read <message-id> --toon

# Read with raw HTML
outlook-cli read <message-id> --no-markdown --toon
```

### Open in Outlook
```bash
# Open email in browser (Outlook on the web)
outlook-cli open <message-id>

# Print URL without opening browser
outlook-cli open <message-id> --no-browser
```

### Search Emails
```bash
# By sender
outlook-cli find --from alice@example.com --toon

# By date range
outlook-cli find --after 2025-01-01 --before 2025-01-31 --toon

# By subject and sender
outlook-cli find --from boss@example.com --subject "budget" --toon

# With attachments
outlook-cli find --has-attachments --after 2025-01-01 --toon
```

### Organize Emails
```bash
# List folders
outlook-cli list --toon

# Move to folder
outlook-cli move <message-id> Archive --toon

# Mark as read
outlook-cli mark <message-id> --read --toon

# Flag for follow-up
outlook-cli mark <message-id> --flag --toon
```

### Bulk Operations
```bash
# Archive old emails
outlook-cli move --all --before 2025-01-01 Archive --toon

# Mark sender's emails as read
outlook-cli mark --all --from newsletter@example.com --read --toon

# Move read emails from inbox to archive
outlook-cli move --all --folder Inbox --only-read Archive --toon
```

Run `outlook-cli --help` for all commands and flags. Run `outlook-cli <command> --help` for detailed options.

## Workflows

1. **Email Triage** ([workflows/email-triage.md](workflows/email-triage.md))
   - Review unread emails systematically
   - Take action: read, archive, flag, skip

2. **Email Search** ([workflows/search-emails.md](workflows/search-emails.md))
   - Build queries from natural language
   - Filter and refine results

3. **Priority Review** ([workflows/priority-review.md](workflows/priority-review.md))
   - Search based on user priorities
   - Rank by relevance

4. **Evidence Review** ([workflows/evidence-review.md](workflows/evidence-review.md))
   - Systematic search for fact-checking
   - Categorize findings

5. **Open Loops Review** ([workflows/open-loops-review.md](workflows/open-loops-review.md))
   - Check flagged emails and pending items
   - Cross-references calendar for invites

## Troubleshooting

**Authentication errors:**
```bash
rm ~/.ai-pim-utils/auth-cache
outlook-cli find --limit 1  # Triggers re-authentication
```

**Command not found:**
- macOS/Linux: Add `~/.local/bin` to PATH
- Windows: Restart PowerShell after installation

**No results from search:**
- Check date format (use YYYY-MM-DD)
- Try broader filters first
- Verify folder name with `outlook-cli list`

**See:** [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/) for more troubleshooting.
