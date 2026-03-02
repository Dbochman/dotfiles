# Email Triage Workflow

This prompt guides you through helping users triage their email inbox efficiently using `outlook-cli`.

## Workflow Overview

1. Check unread email count
2. Show recent unread emails with key details
3. For each email, offer actions
4. Execute user's chosen actions
5. Offer bulk operations if applicable
6. Provide summary

## Step-by-Step Process

### Step 1: Check Unread Count

**CRITICAL: Always check count first to determine output strategy**

```bash
# Get count without displaying all data (prevents truncation)
COUNT=$(outlook-cli find --only-unread --json | jq '.metadata.count')
echo "Total unread: $COUNT"
```

This approach uses JSON+jq to extract only the count, avoiding truncation issues with large datasets.

**Present to user:**
```
Found [N] unread emails in your inbox.
```

**Determine output strategy based on count:**
- If COUNT ≤ 50: Safe to use TOON+--fields for token efficiency
- If COUNT > 50: Use JSON+jq to avoid truncation and control output
- If COUNT > 200: Definitely use JSON+jq with sampling

If there are 0 unread emails:
```
Your inbox is empty! No unread emails to triage.
```
End workflow here.

If there are more than 50 unread emails, ask:
```
You have [N] unread emails. Would you like to:
1. Triage recent emails (last 10)
2. Triage by sender or subject
3. Bulk archive old emails first
```

### Step 2: Show Recent Unread Emails

**Choose format based on count from Step 1:**

**For small datasets (COUNT ≤ 50):**
```bash
# Use TOON+--fields for token efficiency
outlook-cli find --only-unread --toon --fields id,subject,from.emailAddress.address,receivedDateTime,hasAttachments
```

**For large datasets (COUNT > 50):**
```bash
# Use JSON+jq to sample first 10 without truncation
outlook-cli find --only-unread --json | jq -r '.data[:10] | .[] | "\(.id)|\(.subject)|\(.from.emailAddress.address)|\(.receivedDateTime)|\(.hasAttachments)"'
```

⚠️ **NEVER use --limit with large datasets when you need to sample**, because:
- `--limit 10` fetches only 10 emails from API (might miss recent ones)
- `jq '.data[:10]'` fetches all, then samples first 10 (sees full dataset)

Parse the response and present each email in a clear, scannable format:

```
Here are your 10 most recent unread emails:

1. From: boss@company.com
   Subject: Q4 Planning Meeting
   Received: 2 hours ago
   Preview: Please review the attached agenda...
   Has attachments: Yes

2. From: newsletter@example.com
   Subject: Weekly Tech News
   Received: 5 hours ago
   Preview: This week's top stories...
   Has attachments: No

3. From: colleague@company.com
   Subject: Re: Project Update
   Received: 1 day ago
   Preview: Thanks for the update. I have a few...
   Has attachments: No

[... continue for all emails ...]
```

### Step 3: Offer Actions for Each Email

For each email, ask the user what they want to do. Present options clearly:

```
Email #1: [Subject]

What would you like to do?
  [R] Read full email
  [O] Open in Outlook (browser)
  [A] Archive (move to Archive folder)
  [M] Mark as read (keep in Inbox)
  [F] Flag for follow-up
  [D] Delete (move to Deleted Items)
  [S] Skip (do nothing, move to next)
  [Q] Quit triage

Your choice:
```

### Step 4: Execute Chosen Action

Based on user's choice, execute the appropriate command:

**[R] Read full email:**
```bash
# Always use TOON for single email reads (most efficient)
outlook-cli read <message-id> --toon
```

Display the full email content:
```
From: [sender]
To: [recipients]
Subject: [subject]
Date: [date]
Attachments: [list of attachments]

[email body]

---
After reading, what would you like to do?
  [A] Archive
  [M] Mark as read
  [F] Flag for follow-up
  [D] Delete
  [S] Skip to next email
```

**[O] Open in Outlook:**
```bash
# Opens in default browser; user can reply, forward, etc. in OWA
outlook-cli open <message-id>
```

Confirm:
```
Opened in browser: [Subject]
```

**[A] Archive:**
```bash
# Use TOON for individual operations (token efficient)
outlook-cli move <message-id> Archive --toon
```

Confirm:
```
Archived: [Subject]
```

**[M] Mark as read:**
```bash
outlook-cli mark <message-id> --read --toon
```

Confirm:
```
Marked as read: [Subject]
```

**[F] Flag for follow-up:**
```bash
outlook-cli mark <message-id> --flag --toon
```

Confirm:
```
Flagged for follow-up: [Subject]
```

**[D] Delete:**
```bash
outlook-cli move <message-id> "Deleted Items" --toon
```

Confirm:
```
Deleted: [Subject]
```

**[S] Skip:**
Move to next email without any action.

**[Q] Quit:**
Jump to summary (Step 6).

### Step 5: Offer Bulk Operations

After triaging a few emails, identify patterns and offer bulk operations:

**If user has archived multiple emails from the same sender:**
```
I noticed you've archived several emails from [sender].
Would you like to archive all unread emails from this sender?
  [Y] Yes, archive all from [sender]
  [N] No, continue manual triage
```

If Yes:
```bash
# Use TOON for bulk operations (token efficient feedback)
outlook-cli move --all --from sender@example.com --only-unread Archive --toon
```

**If user has marked multiple old emails as read:**
```
I noticed you're marking older emails as read.
Would you like to mark all emails older than [date] as read?
  [Y] Yes, mark all before [date] as read
  [N] No, continue manual triage
```

If Yes:
```bash
outlook-cli mark --all --before YYYY-MM-DD --only-unread --read --toon
```

**Common bulk operation suggestions:**

1. Archive all read emails:
```bash
outlook-cli move --all --only-read Archive --toon
```

2. Archive newsletters (if pattern detected):
```bash
outlook-cli move --all --from newsletter@example.com Archive --toon
```

3. Mark all old unread as read:
```bash
outlook-cli mark --all --before YYYY-MM-DD --only-unread --read --toon
```

4. Delete all emails in Deleted Items older than 30 days:
```bash
outlook-cli move --all --folder "Deleted Items" --before YYYY-MM-DD Archive --toon
```

### Step 6: Provide Summary

After triaging is complete, provide a summary of actions taken:

```
Triage Complete!

Summary:
- Emails processed: [N]
- Archived: [N]
- Marked as read: [N]
- Flagged: [N]
- Deleted: [N]
- Skipped: [N]

Remaining unread: [N]

Would you like to:
- Continue triaging more emails
- Perform bulk operations on remaining emails
- Exit triage
```

## Error Handling

### Authentication Errors
If you receive `AUTH_FAILED`:
```
Authentication failed. Your session may have expired.
Please run: outlook-cli list
Follow the authentication prompt, then try again.
```

### Not Found Errors
If you receive `NOT_FOUND` when trying to move/mark:
```
Email not found. It may have been moved or deleted already.
Continuing to next email...
```

### Network Errors
If you receive `NETWORK_ERROR`:
```
Network connection failed. Please check your internet connection.
Would you like to:
- Retry the operation
- Skip this email
- Exit triage
```

### Rate Limiting
If you receive `RATE_LIMIT`:
```
Microsoft Graph API rate limit exceeded.
Pausing for 60 seconds before continuing...
```

Wait 60 seconds, then retry the operation.

## Handling Truncation During Triage

### Detecting Truncation

If you see truncation indicators during triage:
- `... (output truncated)`
- `[Output truncated to N lines]`
- Incomplete TOON tables (data rows don't match header count)
- Terminal buffer overflow

**IMMEDIATELY switch strategies:**

```bash
# ❌ BAD: Continue with truncated data, assuming you've seen all emails
# This leads to missing emails and incorrect counts

# ✅ GOOD: Detect truncation and switch to JSON+jq
COUNT=$(outlook-cli find --only-unread --json | jq '.metadata.count')
echo "Actual total: $COUNT unread emails"

# Sample first 10 for triage using jq
outlook-cli find --only-unread --json | jq -r '.data[:10] | .[] | "\(.id) | \(.subject) | \(.from.emailAddress.address)"'
```

### Recovery from Truncation

**If truncation detected mid-triage:**

1. **Stop and reassess:**
   ```
   ⚠️ Output truncation detected! The displayed list is incomplete.

   Let me get the accurate count...
   [Run JSON+jq count check]

   You actually have [N] unread emails (not the [M] shown).

   Would you like to:
   - Continue with filtered view (recent emails only)
   - Apply filters to narrow down (by sender, date, etc.)
   - Use bulk operations to process in batches
   ```

2. **Switch to filtered approach:**
   ```bash
   # Narrow down by date
   COUNT=$(outlook-cli find --only-unread --after 2025-11-01 --json | jq '.metadata.count')

   # If still too large, add more filters
   COUNT=$(outlook-cli find --only-unread --after 2025-11-01 --from important@domain.com --json | jq '.metadata.count')
   ```

3. **Use batch processing:**
   ```bash
   # Get all IDs using JSON (handles large datasets)
   ALL_IDS=$(outlook-cli find --only-unread --json | jq -r '.data[] | .id')

   # Process in batches of 10 for triage
   echo "$ALL_IDS" | head -10 | while read -r id; do
     # Show email details and ask user what to do
     outlook-cli read "$id" --toon
   done
   ```

## Tips for Effective Triage

1. **Always check count first**: Use JSON+jq to get accurate count before deciding strategy
2. **Start with small batches**: Triage 10 emails at a time to avoid overwhelm
3. **Watch for truncation**: If output looks cut off, switch to JSON+jq immediately
4. **Look for patterns**: Identify common senders or subjects that can be bulk-processed
5. **Use filters**: If triaging specific topics, use `--subject` or `--from` filters upfront
6. **Archive liberally**: When in doubt, archive (don't delete) - emails remain searchable
7. **Flag important items**: Use flags for emails that need follow-up action
8. **Bulk operations last**: Do manual triage first to identify patterns, then bulk process
9. **Never assume completeness**: If COUNT > 50, assume truncation risk and use JSON+jq

## Advanced Triage Scenarios

### Triage by Sender
If user wants to triage emails from a specific person:
```bash
outlook-cli find --from sender@example.com --only-unread --json
```

### Triage by Date Range
If user wants to triage old emails:
```bash
outlook-cli find --before 2025-01-01 --only-unread --json
```

### Triage with Attachments Only
If user wants to review emails with attachments:
```bash
outlook-cli find --has-attachments --only-unread --json
```

### Triage Specific Folder
If user wants to triage a specific folder:
```bash
outlook-cli find --folder "My Projects" --only-unread --json
```

## Example Conversation

**User:** Triage my inbox

**Assistant:** Let me check your inbox...

Found 23 unread emails. Here are the 10 most recent:

1. From: boss@company.com
   Subject: Q4 Planning Meeting
   Received: 2 hours ago
   Has attachments: Yes

2. From: newsletter@tech.com
   Subject: Weekly Digest
   Received: 4 hours ago
   Has attachments: No

[... continue ...]

What would you like to do with email #1 (Q4 Planning Meeting)?
[R] Read / [A] Archive / [M] Mark read / [F] Flag / [D] Delete / [S] Skip / [Q] Quit

**User:** R

**Assistant:** [Shows full email content]

After reading, what would you like to do?
[A] Archive / [M] Mark read / [F] Flag / [D] Delete / [S] Skip

**User:** F

**Assistant:** Flagged for follow-up: Q4 Planning Meeting

Moving to email #2 (Weekly Digest)...
What would you like to do?
[R] Read / [A] Archive / [M] Mark read / [F] Flag / [D] Delete / [S] Skip / [Q] Quit

**User:** A

**Assistant:** Archived: Weekly Digest

Moving to email #3...

[... continue until user chooses Q or all emails are processed ...]

**Assistant:** Triage Complete!

Summary:
- Emails processed: 10
- Archived: 7
- Flagged: 2
- Skipped: 1

Remaining unread: 13

I noticed you archived most newsletters. Would you like to archive all unread emails from newsletter@tech.com?
[Y] Yes / [N] No

**User:** Y

**Assistant:** Archived 5 additional emails from newsletter@tech.com.

Remaining unread: 8

Would you like to continue triaging?
