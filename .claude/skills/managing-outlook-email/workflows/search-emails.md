# Email Search Workflow

This prompt guides you through helping users search and find emails using `outlook-cli` with advanced filters and queries.

## Workflow Overview

1. Parse user's search criteria
2. Build optimal query with filters
3. Execute search
4. Present results clearly
5. Offer follow-up actions

## Step-by-Step Process

### Step 1: Parse User's Search Criteria

Extract search parameters from natural language queries:

**Common search patterns:**

"Find emails from [person]"
→ `--from [email]`

"Show me emails about [topic]"
→ `--subject [topic]` or `--body [topic]`

"Get emails with attachments from [person]"
→ `--from [email] --has-attachments`

"Find messages I received last week"
→ `--after [last Monday] --before [this Monday]`

"Show unread emails in [folder]"
→ `--folder [name] --only-unread`

"Find emails where I was CC'd"
→ `--cc [my email]`

"Show emails to [person] with [keyword] in subject"
→ `--to [email] --subject [keyword]`

**Extract these parameters:**
- **Sender**: `--from <email>`
- **Recipient (To)**: `--to <email>`
- **Recipient (CC)**: `--cc <email>`
- **Any participant**: `--participants <email>` (From, To, or CC)
- **Subject keywords**: `--subject <text>`
- **Body keywords**: `--body <text>`
- **Date range**: `--after <YYYY-MM-DD>` and/or `--before <YYYY-MM-DD>`
- **Attachments**: `--has-attachments`
- **Read status**: `--only-read` or `--only-unread`
- **Folder**: `--folder <name>` or `--folder-id <id>`
- **Limit**: `--limit <N>` (default: 100,000)

### Step 2: Build Optimal Query

Combine filters into an effective `outlook-cli find` command.

**Key considerations:**

1. **Mutually exclusive flags:**
   - Can't use `--to` or `--cc` with `--participants`
   - Can't use `--only-read` with `--only-unread`
   - Can't use `--folder` with `--folder-id`

2. **Text search limitation:**
   - When combining structured filters (`--only-unread`, `--has-attachments`, dates) with text searches (`--subject`, `--from`, `--to`, `--cc`), the `--limit` is applied BEFORE client-side text filtering
   - For predictable results with text searches, either:
     - Use default limit (100,000)
     - Use a large `--limit` value
     - Or don't combine with other filters

3. **Filter precedence:**
   - Start with folder if specified
   - Add read status filter
   - Add date range
   - Add sender/recipient filters
   - Add content filters (subject/body)
   - Add attachment filter
   - Set limit last

**Example query building:**

User: "Find unread emails from my boss about the project"
→ Filters: `--only-unread --from boss@company.com --subject project`
→ Command:
```bash
outlook-cli find --only-unread --from boss@company.com --subject project --json
```

User: "Show me all emails with attachments from last month"
→ Filters: `--has-attachments --after 2025-10-01 --before 2025-11-01`
→ Command:
```bash
outlook-cli find --has-attachments --after 2025-10-01 --before 2025-11-01 --json
```

User: "Find emails where Sarah was CC'd containing 'budget'"
→ Filters: `--cc sarah@company.com --subject budget` (or `--body budget`)
→ Command:
```bash
outlook-cli find --cc sarah@company.com --subject budget --json
```

### Step 3: Execute Search

**CRITICAL: Check count first to determine output strategy**

```bash
# Step 3a: Get count to decide format
COUNT=$(outlook-cli find [filters] --json | jq '.metadata.count')
echo "Found $COUNT emails"
```

**Choose output format based on count:**
- COUNT ≤ 20: Use TOON+--fields for detailed view
- COUNT 21-100: Use TOON+--fields for compact view or JSON+jq for sampling
- COUNT > 100: Use JSON+jq to avoid truncation

**Step 3b: Execute search with appropriate format**

```bash
# For small datasets (≤20):
outlook-cli find [filters] --toon --fields id,subject,from.emailAddress.address,receivedDateTime,hasAttachments

# For large datasets (>100):
outlook-cli find [filters] --json | jq -r '.data[:20] | .[] | "\(.id)|\(.subject)|\(.from.emailAddress.address)"'
```

Parse the response:

**Success response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "AAMkAGI...",
      "subject": "Q4 Budget Review",
      "from": "boss@company.com",
      "received_date": "2025-11-08T14:30:00Z",
      "is_read": false,
      "has_attachments": true,
      "preview": "Please review the attached budget..."
    }
  ],
  "metadata": {
    "count": 15,
    "timestamp": "2025-11-09T10:00:00Z"
  }
}
```

**Empty results:**
```json
{
  "success": true,
  "data": [],
  "metadata": {
    "count": 0
  }
}
```

**Error response:**
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid email address in --from filter"
  }
}
```

### Step 4: Present Results Clearly

Format search results for easy scanning:

**Format Option 1: Detailed List (for <20 results)**

```
Found 5 emails matching your search:

1. From: boss@company.com
   Subject: Q4 Budget Review
   Date: Nov 8, 2025 @ 2:30 PM
   Status: Unread
   Attachments: Yes (budget.xlsx, presentation.pdf)
   Preview: Please review the attached budget proposal...
   ID: AAMkAGI...xyz

2. From: finance@company.com
   Subject: Re: Budget Planning
   Date: Nov 7, 2025 @ 9:15 AM
   Status: Read
   Attachments: No
   Preview: Thanks for sharing. I have some feedback...
   ID: AAMkAGI...abc

[... continue for all results ...]

---
Total: 5 emails
Unread: 2
With attachments: 3
```

**Format Option 2: Compact List (for 20-100 results)**

```
Found 45 emails matching your search (showing first 20):

1.  [U] Nov 8 - boss@company.com - Q4 Budget Review [📎]
2.  [R] Nov 7 - finance@company.com - Re: Budget Planning
3.  [U] Nov 6 - cfo@company.com - Budget Approval Needed [📎]
4.  [R] Nov 5 - team@company.com - FYI: Budget Timeline
...

Legend: [U] = Unread, [R] = Read, [📎] = Has attachments

Total: 45 emails (20 shown)
Would you like to:
- See the next 20 results
- Read a specific email (enter number)
- Refine your search
```

**Format Option 3: Summary (for >100 results)**

**⚠️ For large result sets, ALWAYS use JSON+jq to avoid truncation:**

```bash
# Get accurate count
COUNT=$(outlook-cli find [filters] --json | jq '.metadata.count')

# Analyze without displaying all data
UNREAD=$(outlook-cli find [filters] --json | jq '[.data[] | select(.isRead == false)] | length')
WITH_ATTACH=$(outlook-cli find [filters] --json | jq '[.data[] | select(.hasAttachments == true)] | length')

# Get top senders (without truncation)
outlook-cli find [filters] --json | jq -r '.data[].from.emailAddress.address' | sort | uniq -c | sort -rn | head -5
```

Present summary:
```
Found 342 emails matching your search.

Summary:
- Date range: Oct 1, 2025 - Nov 8, 2025
- Unread: 23 (7%)
- With attachments: 145 (42%)

Top senders:
1. newsletter@tech.com (89 emails)
2. team@company.com (67 emails)
3. boss@company.com (34 emails)

Would you like to:
- Narrow your search (add more filters)
- Show first 20 results (using JSON+jq sampling)
- Bulk archive old emails
- Save this search
```

**Zero results:**

```
No emails found matching your criteria.

Search: --from boss@company.com --subject "quarterly review" --after 2025-11-01

Suggestions:
1. Try broader search terms (remove some filters)
2. Check sender email address spelling
3. Expand date range
4. Search in different folder

Would you like to:
- Modify the search
- Search all folders
- Try a different query
```

### Step 5: Offer Follow-Up Actions

Based on search results, offer relevant actions:

**For 1-10 results:**
```
What would you like to do?

[1] Read email #[N] (display full content)
[2] Mark all as read
[3] Archive all
[4] Move all to folder
[5] Flag all for follow-up
[6] Refine search (add/remove filters)
[7] Save search for later
```

**For 11-100 results:**
```
What would you like to do?

[1] Show next page of results
[2] Read specific email (enter number)
[3] Narrow search (add more filters)
[4] Bulk operations (archive, mark read, etc.)
[5] Export results to CSV
[6] Save search
```

**For >100 results:**
```
Large result set (342 emails). Recommendations:

[1] Narrow search - Add more filters to reduce results
[2] Bulk archive - Archive all results at once
[3] Bulk mark read - Mark all as read
[4] Group by sender - Show results grouped by sender
[5] Group by date - Show results grouped by month
```

## Common Search Scenarios

### Scenario 1: Find Recent Emails from Specific Person

**User query:** "Show me emails from John this week"

**Extract:**
- Sender: john@company.com (may need to ask for full email)
- Date: this week (calculate Monday to today)

**Build query:**
```bash
outlook-cli find --from john@company.com --after 2025-11-04 --json
```

**Present:**
```
Found 8 emails from john@company.com this week:

1. Nov 8 - Project Update [Unread]
2. Nov 7 - Re: Meeting Notes [Read]
3. Nov 6 - Quick Question [Read]
...

What would you like to do?
```

### Scenario 2: Find Emails with Attachments

**User query:** "Find all emails with attachments from last month"

**Extract:**
- Attachments: yes
- Date: last month (Oct 1 - Oct 31)

**Build query:**
```bash
outlook-cli find --has-attachments --after 2025-10-01 --before 2025-11-01 --json
```

**Present:**
```
Found 47 emails with attachments from October 2025.

Top senders with attachments:
1. boss@company.com (12 emails)
2. clients@external.com (8 emails)
3. reports@system.com (15 emails)

Would you like to:
- See detailed list
- Archive all report emails
- Download specific attachments
```

### Scenario 3: Find Unread Emails in Specific Folder

**User query:** "Show unread emails in my Projects folder"

**Extract:**
- Folder: Projects
- Read status: unread

**Build query:**
```bash
outlook-cli find --folder Projects --only-unread --json
```

**Present:**
```
Found 5 unread emails in "Projects" folder:

1. Nov 8 - Project Alpha Update
2. Nov 7 - Budget Approval Required
...

Would you like to triage these emails?
```

### Scenario 4: Find Emails by Subject Keyword

**User query:** "Find emails about the AWS migration"

**Extract:**
- Subject: "AWS migration"

**Build query:**
```bash
outlook-cli find --subject "AWS migration" --json
```

**Note:** Since this uses text search, use default limit for predictable results.

**Present:**
```
Found 12 emails with "AWS migration" in subject:

1. [U] Nov 8 - AWS Migration Timeline
2. [R] Nov 5 - Re: AWS Migration Budget
3. [R] Nov 3 - AWS Migration Kickoff Meeting
...
```

### Scenario 5: Find Emails Sent to Specific Person

**User query:** "Show emails I sent to the CEO"

**Extract:**
- Recipient (To): ceo@company.com
- Folder: Sent Items

**Build query:**
```bash
outlook-cli find --folder "Sent Items" --to ceo@company.com --json
```

**Present:**
```
Found 6 emails you sent to ceo@company.com:

1. Nov 7 - Q4 Executive Summary
2. Oct 25 - Project Status Update
3. Oct 12 - Budget Proposal
...
```

### Scenario 6: Find Old Emails for Cleanup

**User query:** "Find emails older than 6 months"

**Extract:**
- Date: before 6 months ago (before 2025-05-09)

**Build query:**
```bash
outlook-cli find --before 2025-05-09 --json
```

**Present:**
```
Found 1,247 emails older than 6 months.

Summary:
- Oldest: Jan 15, 2024
- Unread: 34 (3%)
- With attachments: 423 (34%)

Recommendations:
1. Archive all read emails
2. Delete emails older than 1 year
3. Review and archive attachments

Would you like to:
- Archive all old emails
- Delete very old emails
- Review by sender first
```

## Advanced Search Techniques

### Boolean AND (implicit)

Multiple filters are ANDed together:

```bash
# Find emails from boss AND with attachments AND unread
outlook-cli find --from boss@company.com --has-attachments --only-unread --json
```

### Searching Multiple Senders

Current limitation: `--from` accepts only one email address.

Workaround: Run multiple searches and combine results:

```bash
# Search for sender 1
outlook-cli find --from alice@company.com --json

# Search for sender 2
outlook-cli find --from bob@company.com --json
```

Present combined results:
```
Found emails from Alice (5) and Bob (8):

From Alice:
1. Nov 8 - Project Update
...

From Bob:
1. Nov 8 - Meeting Request
...
```

### Date Range Calculations

**Relative dates:**
- Today: current date
- Yesterday: today - 1 day
- This week: last Monday to today
- Last week: 2 Mondays ago to last Monday
- This month: first day of month to today
- Last month: first day of last month to last day of last month

**Examples:**
```bash
# Last 7 days
--after 2025-11-01 --before 2025-11-09

# Last 30 days
--after 2025-10-09 --before 2025-11-09

# This year
--after 2025-01-01
```

### Folder Search

**Well-known folders:**
- inbox
- sent (or "Sent Items")
- drafts
- archive
- deleteditems (or "Deleted Items")
- junk (or "Junk Email")

**Custom folders:**
Use exact folder name (case-insensitive):
```bash
outlook-cli find --folder "My Projects" --json
```

**Searching all folders:**
Omit `--folder` flag to search across all folders.

### Participant Search

Use `--participants` to find emails where someone is From, To, or CC:

```bash
# Find any email involving boss@company.com
outlook-cli find --participants boss@company.com --json
```

This is useful when you don't know if you sent or received the email.

## Error Handling

### Invalid Email Address
```
Error: Invalid email address in --from filter.

Please provide a valid email address like: user@example.com

Would you like to correct it?
```

### Invalid Date Format
```
Error: Invalid date format.

Please use YYYY-MM-DD format. Examples:
- 2025-11-09
- 2025-01-15

Would you like to try again?
```

### Folder Not Found
```
Folder "Projecst" not found.

Did you mean:
1. Projects
2. Prospects
3. Archive

Which folder did you mean? [1/2/3]
```

Alternative: List available folders:
```bash
outlook-cli list --json
```

### No Permission
```
Error: Permission denied for folder "Manager's Folder".

You don't have access to this folder. Please search in:
- Your own folders (use: outlook-cli list)
- Shared folders you have access to
```

### Authentication Failed
```
Authentication failed. Your session may have expired.

Please run: outlook-cli list
Follow the authentication prompt, then try searching again.
```

### Rate Limiting
```
Microsoft Graph API rate limit exceeded.

Too many search requests in a short time. Please wait 60 seconds.

[Retrying in 60 seconds...]
```

## Handling Truncation in Search Results

### Detecting Large Result Sets

**ALWAYS check count before displaying results:**

```bash
# Get count first
COUNT=$(outlook-cli find [filters] --json | jq '.metadata.count')

if [ "$COUNT" -gt 100 ]; then
  echo "⚠️ Large result set detected: $COUNT emails"
  echo "Using JSON+jq to avoid truncation..."

  # Sample first 20 for preview
  outlook-cli find [filters] --json | jq -r '.data[:20] | .[] | "\(.subject) | \(.from.emailAddress.address)"'

  echo "... (showing first 20 of $COUNT)"
else
  echo "✓ Manageable result set: $COUNT emails"
  # Safe to use TOON for full display
  outlook-cli find [filters] --toon --fields id,subject,from,receivedDateTime
fi
```

### Recovery from Truncation

**If truncation occurs mid-search:**

1. **Stop and inform user:**
   ```
   ⚠️ Search returned too many results ($COUNT emails).
   Output would be truncated.

   Recommendations:
   - Add more filters to narrow results
   - View summary statistics instead of full list
   - Use bulk operations on filtered set
   ```

2. **Suggest filters to narrow:**
   ```bash
   # Too many results from broad search
   COUNT=$(outlook-cli find --from "@company.com" --json | jq '.metadata.count')
   # Returns: 500 emails

   # Narrow by date
   COUNT=$(outlook-cli find --from "@company.com" --after 2025-11-01 --json | jq '.metadata.count')
   # Returns: 45 emails (manageable!)
   ```

3. **Use sampling for preview:**
   ```bash
   # Show sample instead of all results
   echo "Showing 10 most recent of $COUNT emails:"
   outlook-cli find [filters] --json | jq -r '.data[:10] | .[] | "\(.receivedDateTime) | \(.subject)"'
   ```

## Tips for Effective Searching

1. **Always check count first**: Use JSON+jq to get accurate count before deciding display strategy
2. **Start broad, then narrow**: Begin with general search, add filters to refine
3. **Watch for truncation**: If COUNT > 100, use JSON+jq instead of TOON
4. **Use date ranges for large mailboxes**: Narrow down by time period first
5. **Combine filters strategically**: sender + date is usually faster than subject search
6. **Save common searches**: Document frequently used filter combinations
7. **Use folder filters**: Search specific folders when possible
8. **Check multiple folders**: Search sent/inbox separately if needed
9. **Use participants for bidirectional search**: Find all communication with a person
10. **Mind the limit with text searches**: Use default limit when combining with --subject/--body
11. **Never assume completeness**: If output looks cut off, it probably is - switch to JSON+jq

## Example Conversations

### Example 1: Simple Search

**User:** Find emails from my boss

**Assistant:** I'll search for emails from your boss. What's their email address?

**User:** boss@company.com

**Assistant:** Searching for emails from boss@company.com...

Found 127 emails from boss@company.com.

Most recent 10:
1. [U] Nov 8 - Q4 Planning Meeting
2. [R] Nov 7 - Re: Budget Approval
3. [U] Nov 6 - Action Items from Review
...

Would you like to:
- See more results
- Filter by date range
- Show only unread
- Read a specific email

### Example 2: Complex Search with Refinement

**User:** Find emails about the AWS project

**Assistant:** Searching for "AWS project"...

Found 234 emails with "AWS project" in subject or body.

That's a lot! Would you like to narrow it down?
- By date (e.g., last month)
- By sender (specific person)
- Only unread
- Only with attachments

**User:** Last month only

**Assistant:** Refining search to October 2025...

Found 23 emails about "AWS project" from last month:

1. Oct 28 - AWS Migration Complete [📎]
2. Oct 25 - AWS Cost Analysis
3. Oct 20 - Re: AWS Timeline
...

Much better! What would you like to do?
[1] Read email #1
[2] Show all emails from this sender
[3] Mark all as read
[4] Archive old project emails

**User:** 1

**Assistant:** [Displays full email content for #1]

From: engineering@company.com
Subject: AWS Migration Complete
Date: Oct 28, 2025
...

Would you like to:
- Reply to this email
- Archive this email
- Continue reviewing search results
