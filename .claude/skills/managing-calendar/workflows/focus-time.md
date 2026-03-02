# Focus Time Management Workflow

This prompt guides you through creating focus time by analyzing and reorganizing your calendar.

## Use Case

User wants protected time for deep work:
- "I need 4 hours of focus time today"
- "Clear my calendar for the afternoon"
- "Help me find time for deep work this week"

## Current Capabilities

| Feature | Support | How |
|---------|---------|-----|
| View schedule | ✅ | `calendar-cli find` |
| Decline meetings | ✅ | `calendar-cli respond decline` |
| Create focus blocks | ✅ | `calendar-cli create` |
| Evaluate priorities | ✅ | Claude reasons about criteria |

## ⚠️ CRITICAL GUARDRAILS

**This workflow involves declining meetings, which affects other people.**

**NEVER:**
- Auto-decline meetings without explicit user confirmation
- Decline 1:1s with leadership without discussion
- Decline customer/external meetings
- Execute plan without showing it first

**ALWAYS:**
- Show the complete plan before any action
- Explain reasoning for each suggestion
- Get explicit "yes" before declining anything
- Provide undo guidance

## Workflow Overview

1. Understand requirements
2. Analyze current schedule
3. Identify gaps and moveable meetings
4. Propose plan (DO NOT EXECUTE)
5. Execute only after confirmation
6. Handle fallbacks

## Step-by-Step Process

### Step 1: Understand Requirements

**Clarify user's needs:**
```
To help you find focus time, I need to understand:

1. How much time do you need? (e.g., 4 hours ideal, 2 hours minimum)
2. When? (today, this week, specific days)
3. What criteria for declining meetings?
   - Can I suggest declining optional meetings?
   - Any meetings that are absolutely protected?
4. Do you want me to create calendar blocks, or just identify time?
```

**Example criteria to establish:**
- ✅ Can decline: Large optional meetings, recordings available
- ⚠️ Ask first: Team meetings, recurring syncs
- ❌ Never decline: 1:1s with manager, customer meetings, interviews

### Step 2: Analyze Current Schedule

**Get today's meetings:**
```bash
# Today's schedule
calendar-cli find --after 2025-01-31 --before 2025-02-01 --toon --fields id,subject,start.dateTime,end.dateTime,organizer.emailAddress.name,isAllDay
```

**Map the day:**
```
Today's Schedule (January 31, 2025):

08:00-08:30  ☕ Free
08:30-09:00  Team Standup (required - daily sync)
09:00-10:00  ☕ Free  
10:00-11:00  Project Review with Sarah (1:1, keep)
11:00-12:00  All-Hands Meeting (optional attendance, recording available)
12:00-13:00  ☕ Lunch
13:00-14:00  Sprint Planning (required - team ceremony)
14:00-15:00  Newsletter Review (optional, can decline)
15:00-16:00  ☕ Free
16:00-17:00  Customer Demo (external - cannot decline)

Current free time: 2.5 hours (fragmented)
Target: 4 hours focus time
Gap: 1.5 hours needed
```

### Step 3: Categorize Meetings

**Assign decline risk:**

| Meeting | Category | Reason |
|---------|----------|--------|
| Team Standup | 🔴 Keep | Daily sync, brief |
| 1:1 with Sarah | 🔴 Keep | 1:1 with teammate |
| All-Hands | 🟡 Could decline | Optional, recording available |
| Sprint Planning | 🔴 Keep | Team ceremony, important |
| Newsletter Review | 🟢 Can decline | Optional, low impact |
| Customer Demo | 🔴 Keep | External, high stakes |

**Legend:**
- 🔴 **Keep**: Cannot/should not decline
- 🟡 **Could decline**: Optional but may have value
- 🟢 **Can decline**: Low impact, easy to skip

### Step 4: Propose Plan (DO NOT EXECUTE)

**Present options clearly:**

```
## Focus Time Plan

⚠️ I will not make any changes without your approval.

### Target: 4 hours of focus time

**PROPOSED ACTIONS:**

📅 DECLINE (requires your approval):
1. 11:00 All-Hands Meeting
   - Reason: Optional attendance, recording will be available
   - Impact: Low - you can watch recording later

2. 14:00 Newsletter Review  
   - Reason: Optional meeting
   - Impact: Low - content shared via email anyway

📅 KEEP (no changes):
- 08:30 Team Standup (required)
- 10:00 1:1 with Sarah (important)
- 13:00 Sprint Planning (team ceremony)
- 16:00 Customer Demo (external)

📅 CREATE (focus blocks):
- 08:00-08:30 Focus Block (before standup)
- 09:00-10:00 Focus Block  
- 11:00-13:00 Focus Block (if All-Hands declined)
- 14:00-16:00 Focus Block (if Newsletter declined)

### Result if approved:
- Focus time: 4.5 hours ✅
- Meetings declined: 2
- Blocks created: 4

### Your options:
1. ✅ Approve all - I'll decline 2 meetings and create focus blocks
2. 🔄 Modify - Tell me what to change
3. ❌ Cancel - No changes made

What would you like to do?
```

### Step 5: Execute Only After Confirmation

**Wait for explicit approval before any action.**

**On approval:**
```bash
# Decline meetings (with comment)
calendar-cli respond <all-hands-id> decline --comment "Protecting focus time, will watch recording" --toon

calendar-cli respond <newsletter-id> decline --comment "Protecting focus time today" --toon

# Create focus blocks
calendar-cli create \
  --subject "🔕 Focus Time - Do Not Book" \
  --start "2025-01-31T09:00:00" \
  --end "2025-01-31T10:00:00" \
  --toon

# Repeat for other blocks...
```

**Confirm completion:**
```
✅ Focus time secured!

Completed actions:
- Declined: All-Hands Meeting (11:00)
- Declined: Newsletter Review (14:00)
- Created: 4 focus blocks totaling 4.5 hours

Your focus blocks today:
- 08:00-08:30
- 09:00-10:00
- 11:00-13:00
- 14:00-16:00

Tip: Set your status to "Do Not Disturb" during these blocks.
```

### Step 6: Handle Fallbacks

**If target isn't achievable:**

```
## Focus Time Analysis

I can only find 2 hours of focus time today without declining protected meetings.

**Maximum available without declining:**
- 09:00-10:00 (1 hour)
- 15:00-16:00 (1 hour)
Total: 2 hours

**To get 4 hours, you'd need to decline:**
- 1:1 with Sarah (not recommended - teammate relationship)
- Sprint Planning (not recommended - team ceremony)

**Options:**
1. Accept 2 hours today
2. Look at tomorrow/this week instead
3. Tell me if any "protected" meetings are actually moveable

What would you like to do?
```

## Example Session

**User:** "I need 4 hours of focus time today, decline what you need to."

**Claude's response:**

```
I can help you find focus time! Let me analyze your schedule first.

⚠️ Important: I'll show you a plan before making any changes.
I will not decline any meetings without your explicit approval.

Let me check today's calendar...
```

```bash
calendar-cli find --after 2025-01-31 --before 2025-02-01 --toon
```

```
I found 6 meetings today. Here's my analysis:

[Shows categorized meeting list]

## Proposed Plan

To get 4 hours of focus time, I suggest declining:
1. All-Hands (11am) - optional, recording available  
2. Newsletter Review (2pm) - optional

This would give you these focus blocks:
- 9-10am (1 hour)
- 11am-1pm (2 hours) 
- 2-4pm (2 hours before customer call)
Total: 5 hours ✅

Should I proceed? Reply:
- "Yes" to approve
- "Keep all-hands" to modify
- "Cancel" to make no changes
```

## Tips and Limitations

**Tips:**
- Start with identifying free time before suggesting declines
- Consider meeting patterns (daily standups are usually short)
- Suggest declining optional/large meetings before important 1:1s
- Always provide undo guidance

**Limitations:**
- Cannot reschedule meetings (only organizer can do that)
- Cannot contact organizers on user's behalf
- Cannot assess true priority (user knows best)
- Focus blocks don't prevent urgent interruptions

**Undo guidance:**
```
To re-accept a declined meeting:
calendar-cli respond <meeting-id> accept --toon

To delete a focus block:
calendar-cli delete <focus-block-id> --toon
```

## Meeting Categories Reference

### 🔴 Do Not Decline (without explicit override)
- 1:1s with manager or leadership
- Customer/external meetings
- Interviews (conducting or being interviewed)
- Time-sensitive deadlines
- Meetings you organized

### 🟡 Ask Before Declining
- Team ceremonies (standups, retros, planning)
- 1:1s with teammates
- Cross-functional syncs
- Training sessions

### 🟢 Generally Safe to Decline
- Large optional meetings (>10 attendees, optional)
- Informational sessions with recordings
- Social events
- Newsletters/updates that come via email anyway

## Related Workflows

- [calendar-review.md](calendar-review.md) - Overall calendar analysis
- [open-loops-review.md](open-loops-review.md) - Pending meeting invites
