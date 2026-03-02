# Open Loops Review Workflow

This workflow helps users identify and manage "open loops" - incomplete tasks, pending commitments, and items requiring follow-up across email and calendar.

## What Are Open Loops?

Open loops are commitments or tasks that aren't yet complete:
- Flagged emails awaiting action
- Unread emails that may need response
- Meeting invites not yet responded to
- Recent emails you sent that may need follow-up

## Workflow Overview

1. Check flagged emails (explicit follow-up markers)
2. Check pending meeting invites
3. Review recent unread emails
4. Optionally check sent emails awaiting replies
5. Summarize and offer actions

## Step-by-Step Process

### Step 1: Check Flagged Emails

Flagged emails are explicit "open loops" - items the user marked for follow-up.

```bash
# Find all flagged emails
outlook-cli find --flagged --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

**Present to user:**
```
📌 Flagged for Follow-up: [N] items

1. [Subject] - from [sender] - flagged [date]
2. ...
```

If no flagged emails:
```
✓ No flagged emails - no explicit follow-up items.
```

### Step 2: Check Pending Meeting Invites

Meeting invites awaiting response are open loops - decisions not yet made.

```bash
# Find events where user hasn't responded (check recent/upcoming events)
calendar-cli find --after [today] --before [2 weeks out] --toon --fields id,subject,organizer.emailAddress.name,start.dateTime,responseStatus
```

Look for events with `responseStatus.response` = "notResponded" or "none".

**Present to user:**
```
📅 Pending Meeting Invites: [N] items

1. [Subject] - from [organizer] - [date/time]
   ↳ Awaiting your response (accept/decline/tentative)
2. ...
```

**Offer quick actions:**
```
Would you like to respond to any of these?
- [A] Accept all
- [R] Review each one
- [S] Skip for now
```

### Step 3: Review Recent Unread Emails

Unread emails may contain action items or requests.

```bash
# Get count first
COUNT=$(outlook-cli find --only-unread --json | jq '.metadata.count')

# If manageable, show them
if [ "$COUNT" -le 20 ]; then
  outlook-cli find --only-unread --toon --fields id,subject,from.emailAddress.address,receivedDateTime
else
  # Show recent subset
  outlook-cli find --only-unread --json | jq -r '.data[:10] | .[] | "\(.subject) - \(.from.emailAddress.address)"'
fi
```

**Present to user:**
```
📬 Unread Emails: [N] items

Recent unread that may need attention:
1. [Subject] - from [sender] - [time ago]
2. ...

Would you like to:
- [T] Triage these emails
- [S] Skip for now
```

### Step 4: (Optional) Check Sent Emails Awaiting Replies

This helps identify conversations where the user is waiting on others.

```bash
# Find recent sent emails (last 7 days)
outlook-cli find --folder "Sent Items" --after [7 days ago] --toon --fields id,subject,to,sentDateTime
```

**Present to user:**
```
📤 Recently Sent (may be awaiting replies): [N] items

1. [Subject] - to [recipient] - sent [date]
2. ...

Note: These are emails you sent recently. Check if any need follow-up.
```

### Step 5: Summarize Open Loops

Provide a consolidated summary:

```
═══════════════════════════════════════
📋 Open Loops Summary
═══════════════════════════════════════

📌 Flagged emails:           [N]
📅 Pending meeting invites:  [N]
📬 Unread emails:            [N]
📤 Sent (potential follow-up): [N]

───────────────────────────────────────
Total items needing attention: [TOTAL]
═══════════════════════════════════════

Would you like to:
1. Address flagged emails first
2. Respond to pending invites
3. Triage unread emails
4. Review sent items
5. Done for now
```

## Quick Open Loops Check

For a fast overview without detailed review:

```bash
# Quick counts
echo "Flagged: $(outlook-cli find --flagged --json | jq '.metadata.count')"
echo "Unread: $(outlook-cli find --only-unread --json | jq '.metadata.count')"
echo "Pending invites: Check calendar for notResponded events"
```

## Tips for Managing Open Loops

1. **Process flagged items regularly** - Don't let the flag list grow stale
2. **Respond to invites promptly** - Even "tentative" is better than no response
3. **Triage unread daily** - Prevents inbox overwhelm
4. **Use flags intentionally** - Flag = "I need to do something with this"
5. **Unflag when done** - `outlook-cli mark <id> --unflag --toon`

## Follow-up Actions

After identifying open loops, common next steps:

**For flagged emails:**
```bash
# Read the email
outlook-cli read <id> --toon

# Take action, then unflag
outlook-cli mark <id> --unflag --toon
```

**For pending invites:**
```bash
# Accept
calendar-cli respond <id> accept --toon

# Decline with reason
calendar-cli respond <id> decline --comment "Schedule conflict" --toon
```

**For unread emails:**
```bash
# Quick triage - see workflows/email-triage.md
outlook-cli read <id> --toon
outlook-cli mark <id> --read --toon  # or --flag if needs follow-up
```

## Related Workflows

- [email-triage.md](email-triage.md) - Detailed inbox triage process
- [calendar-review.md](calendar-review.md) - Calendar review and conflict detection
