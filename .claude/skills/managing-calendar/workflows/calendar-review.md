# Calendar Review Workflow

This prompt guides you through helping users review and analyze their calendar using `calendar-cli`.

## Workflow Overview

1. Determine review scope (today, week, custom range)
2. Fetch and display events
3. Identify issues (conflicts, back-to-back meetings, gaps)
4. Suggest optimizations
5. Offer actions (reschedule, cancel, add prep time)

## Step-by-Step Process

### Step 1: Determine Review Scope

Ask the user what time period they want to review:

```
What would you like to review?
  [1] Today's calendar
  [2] This week
  [3] Next week
  [4] Custom date range

Or just tell me: "show my calendar for [timeframe]"
```

**Calculate date ranges based on user input:**

The CLI requires dates in YYYY-MM-DD format. Calculate the appropriate dates based on the current date and user request:

- **Today**: Use current date as --after, next day as --before
  - Example: `--after 2025-12-17 --before 2025-12-18`

- **This week**: Calculate Monday of current week and next Monday
  - Example: `--after 2025-12-16 --before 2025-12-23`

- **Next week**: Calculate next Monday and the Monday after
  - Example: `--after 2025-12-23 --before 2025-12-30`

- **Custom**: Use the dates provided by user

### Step 2: Fetch and Display Events

**Check count first to determine output strategy:**

```bash
# Get count for the date range
COUNT=$(calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq '.metadata.count')
echo "Found $COUNT events in this period"
```

**Choose format based on count:**
- COUNT ≤ 50: Use TOON+--fields (most calendar reviews fall here)
- COUNT > 50: Use JSON+jq to avoid truncation (rare, very busy calendars)

Execute the find command with appropriate format:

```bash
# For typical calendars (≤50 events):
calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --toon --fields id,subject,start.dateTime,end.dateTime,attendees,location

# For very busy calendars (>50 events):
calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq -r '.data[] | "\(.start.dateTime)|\(.end.dateTime)|\(.subject)|\(.location)"'
```

Parse the response and present events in a clear, organized format:

**Format 1: Today's Calendar**

```
Your calendar for Saturday, November 9, 2025:

9:00 AM - 10:00 AM  Team Standup (1h)
                    with: alice@example.com, bob@example.com
                    Location: Conference Room A

10:00 AM - 11:30 AM Project Review (1.5h)
                    with: manager@example.com
                    Location: Zoom link in details

[30 min gap - Free time]

12:00 PM - 1:00 PM  Lunch with Client (1h)
                    with: client@external.com
                    Location: Downtown Restaurant

1:00 PM - 2:30 PM   Development Time (1.5h) [OVERLAP WARNING]
                    with: (no attendees - focus time)

2:00 PM - 3:00 PM   Emergency Meeting (1h) [CONFLICT!]
                    with: team@example.com
                    Location: Conference Room B

[2 hour gap - Free time]

5:00 PM - 5:30 PM   Daily Wrap-up (30min)
                    with: (no attendees)

---
Total: 6 events
Meeting time: 6.5 hours
Free time: 2.5 hours
Conflicts: 1
```

**Format 2: Weekly View**

```
Your calendar for the week of November 11-15, 2025:

Monday, Nov 11:
  9:00 AM - Team Standup (1h)
  10:00 AM - Project Review (1.5h)
  2:00 PM - Client Call (1h)
  → 3 events, 3.5 hours

Tuesday, Nov 12:
  9:00 AM - Team Standup (1h)
  1:00 PM - Design Review (2h)
  → 2 events, 3 hours

Wednesday, Nov 13:
  9:00 AM - Team Standup (1h)
  11:00 AM - 1:1 with Manager (30min)
  3:00 PM - Sprint Planning (2h)
  → 3 events, 3.5 hours

Thursday, Nov 14:
  9:00 AM - Team Standup (1h)
  [Rest of day free]
  → 1 event, 1 hour

Friday, Nov 15:
  [All day - PTO]
  → 0 events

---
Week Summary:
- Total events: 9
- Total meeting time: 11 hours
- Busiest day: Monday (3.5 hours)
- Lightest day: Friday (PTO)
- Average meetings per day: 1.8
```

### Step 3: Identify Issues

Analyze the calendar for common issues and present findings:

#### A. Scheduling Conflicts

Events that overlap in time:

```
⚠️ CONFLICTS DETECTED:

Saturday, Nov 9 @ 1:00 PM - 2:30 PM:
  - "Development Time" (1:00 PM - 2:30 PM)
  - "Emergency Meeting" (2:00 PM - 3:00 PM)

  Overlap: 30 minutes (2:00 PM - 2:30 PM)

Action needed: Reschedule one of these events.
```

#### B. Back-to-Back Meetings

Events with no gap between them:

```
⚠️ BACK-TO-BACK MEETINGS:

Monday, Nov 11:
  10:00 AM - 11:30 AM: Project Review
  11:30 AM - 12:00 PM: Quick Sync (immediately after)
  12:00 PM - 1:00 PM: Lunch Meeting (immediately after)

  Total: 3 hours without breaks

Recommendation: Add 15-minute buffers between meetings for:
- Bathroom breaks
- Email catch-up
- Mental rest
```

#### C. Over-Scheduled Days

Days with excessive meeting time:

```
⚠️ OVER-SCHEDULED DAYS:

Wednesday, Nov 13: 7 hours of meetings (87% of workday)
  - Very little time for focused work
  - Risk of burnout

Recommendation:
- Decline optional meetings
- Reschedule non-urgent items
- Block focus time
```

#### D. Gaps and Wasted Time

Short gaps that are hard to use productively:

```
💡 FRAGMENTED SCHEDULE:

Tuesday, Nov 12:
  9:00 AM - 10:00 AM: Meeting
  [20 min gap - too short for deep work]
  10:20 AM - 11:00 AM: Meeting
  [30 min gap]
  11:30 AM - 12:30 PM: Meeting

Recommendation: Consolidate meetings to create longer focus blocks.
```

#### E. Missing Prep Time

Important meetings without preparation buffer:

```
💡 PREP TIME NEEDED:

Monday, Nov 11 @ 2:00 PM: Client Presentation
  - No prep time blocked before
  - Previous meeting ends at 1:30 PM (only 30 min gap)

Recommendation: Block 1:30 PM - 2:00 PM for presentation prep.
```

### Step 4: Suggest Optimizations

Based on identified issues, provide actionable suggestions:

#### Conflict Resolution
```
To resolve the conflict on Saturday at 2:00 PM:

Option 1: Reschedule "Emergency Meeting" to 3:00 PM
  - Would you like me to find free time after 2:30 PM?

Option 2: Shorten "Development Time" to end at 2:00 PM
  - Would you like me to update this event?

Option 3: Decline "Emergency Meeting"
  - Would you like me to send a decline response?

Which option do you prefer?
```

#### Meeting Consolidation
```
I noticed you have 3 separate 1:1s on Monday:
  - 10:00 AM with Alice (30 min)
  - 2:00 PM with Bob (30 min)
  - 4:00 PM with Carol (30 min)

Suggestion: Schedule back-to-back 1:1s from 2:00-3:30 PM
  - Frees up morning and late afternoon
  - Creates focused time blocks

Would you like me to suggest this to the attendees?
```

#### Focus Time Protection
```
Your calendar has no focus time blocks this week.

Recommendation: Block 2-hour focus time slots:
  - Monday: 10:00 AM - 12:00 PM
  - Wednesday: 2:00 PM - 4:00 PM
  - Friday: 9:00 AM - 11:00 AM

Focus time is marked as "Busy" so others can't schedule over it.

Would you like me to create these blocks?
```

#### Meeting-Free Days
```
Consider declaring Thursday as a meeting-free day:
  - Currently only 1 standup scheduled
  - Perfect for deep work, coding, or strategic thinking
  - Team productivity benefit

Would you like to:
  - Block Thursday 10 AM - 5 PM as "Focus Day"
  - Set Outlook to auto-decline Thursday meetings
```

### Step 5: Offer Actions

Present actionable next steps:

```
Calendar Review Complete!

Summary:
  ✓ Reviewed 9 events across 5 days
  ⚠️ Found 1 conflict
  ⚠️ Found 3 back-to-back meeting blocks
  💡 Identified 2 optimization opportunities

What would you like to do?

[1] Resolve conflicts
    → I'll help you reschedule overlapping events

[2] Add buffer time
    → I'll insert 15-min breaks between back-to-back meetings

[3] Block focus time
    → I'll create dedicated focus blocks

[4] Decline meetings
    → I'll help you identify which meetings to decline

[5] Export calendar
    → I'll show you how to share your availability

[6] Nothing now
    → Review complete, no changes

Your choice:
```

## Detailed Action Workflows

### Action 1: Resolve Conflicts

For each conflict:

1. Show both events side-by-side
2. Ask which to reschedule
3. Find available time slots
4. Update the event

```bash
# Update event to new time (use TOON for single operations)
calendar-cli update <event-id> \
  --start "YYYY-MM-DDTHH:MM:SS" \
  --end "YYYY-MM-DDTHH:MM:SS" \
  --toon
```

### Action 2: Add Buffer Time

For each back-to-back pair:

1. Identify which meeting can be shortened
2. Update end time to create gap

```bash
# Shorten first meeting by 15 minutes
calendar-cli update <event-id> \
  --end "YYYY-MM-DDTHH:MM:SS" \
  --toon
```

### Action 3: Block Focus Time

Create focus time events:

```bash
# Use TOON for single event creation
calendar-cli create \
  --subject "Focus Time - No Meetings" \
  --start "YYYY-MM-DDTHH:MM:SS" \
  --end "YYYY-MM-DDTHH:MM:SS" \
  --toon
```

### Action 4: Decline Meetings

Help user identify which meetings to decline:

```
Which meetings could you decline?

Optional meetings (you're not the organizer):
  1. Team Social Hour (Friday 4 PM)
  2. Optional Design Review (Wednesday 3 PM)

Low-priority based on attendees:
  3. FYI Meeting (Tuesday 2 PM) - informational only

Overlapping with conflicts:
  4. Emergency Meeting (Saturday 2 PM) - conflicts with Development Time

Which would you like to decline? [1,2,3,4, or comma-separated list]
```

```bash
# Use TOON for single event responses
calendar-cli respond <event-id> decline \
  --comment "Thank you for the invite, but I have a conflict." \
  --toon
```

## Error Handling

### No Events Found
```
Your calendar is clear for [timeframe]!

No meetings scheduled.

Would you like to:
- Review a different time period
- Schedule a meeting
- Exit calendar review
```

### Authentication Errors
```
Authentication failed. Please run: calendar-cli calendars
Follow the authentication prompt, then try again.
```

### Network Errors
```
Unable to fetch calendar events. Please check your internet connection.
Would you like to retry?
```

## Advanced Features

### Compare Multiple Calendars

If user has multiple calendars:

```bash
# List available calendars (use TOON for small list)
calendar-cli calendars --toon

# Fetch events from specific calendar
# Check count first to decide format
COUNT=$(calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --calendar "Work Calendar" --json | jq '.metadata.count')

if [ "$COUNT" -le 50 ]; then
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --calendar "Work Calendar" --toon --fields id,subject,start.dateTime
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --calendar "Personal Calendar" --toon --fields id,subject,start.dateTime
else
  # Use JSON+jq for busy calendars
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --calendar "Work Calendar" --json | jq -r '.data[] | "\(.start.dateTime) | \(.subject)"'
fi
```

Present combined view:
```
Combined Calendar View for Today:

Work Calendar:
  9:00 AM - Team Standup
  2:00 PM - Client Call

Personal Calendar:
  12:00 PM - Doctor Appointment
  6:00 PM - Dinner with Friends

Total: 4 events (2 work, 2 personal)
```

### Travel Time Analysis

For in-person meetings with locations:

```
🚗 TRAVEL TIME ANALYSIS:

Tuesday, Nov 12:
  11:00 AM: Meeting at Downtown Office
  1:00 PM: Meeting at Client Site (15 miles away)

Warning: Only 2 hours between meetings.
Estimated travel time: 30 minutes + parking

Recommendation: Reschedule client meeting to 1:30 PM or later.
```

### Meeting Load Trends

For weekly/monthly reviews:

```
📊 MEETING LOAD TRENDS:

This week: 11 hours (22% increase from last week)
Last week: 9 hours
Two weeks ago: 10 hours

Trend: Increasing ⬆️

Your meeting load is growing. Consider:
- Setting maximum daily meeting hours (e.g., 4 hours)
- Declining low-priority meetings
- Batching meetings on specific days
```

## Tips for Effective Calendar Review

1. **Review regularly**: Daily morning review, weekly planning session
2. **Proactive conflict resolution**: Fix conflicts as soon as they appear
3. **Protect focus time**: Block at least 2-hour chunks for deep work
4. **Add prep time**: Block 15-30 min before important meetings
5. **Batch similar meetings**: Group 1:1s, status updates together
6. **Leave gaps**: 15-30 min between meetings for breaks
7. **Respect work hours**: Avoid early mornings and late evenings
8. **Meeting-free days**: Declare at least one day per week meeting-free

## Example Conversations

### Example 1: Today's Review

**User:** Review my calendar for today

**Assistant:** Let me check your calendar for Saturday, November 9, 2025...

Your calendar for today:

9:00 AM - 10:00 AM  Team Standup (1h)
10:00 AM - 11:30 AM Project Review (1.5h)
12:00 PM - 1:00 PM  Lunch Meeting (1h)
2:00 PM - 3:00 PM   Client Call (1h)

---
Total: 4 events
Meeting time: 4.5 hours
No conflicts detected ✓

Notes:
- Back-to-back meetings from 9:00 AM - 11:30 AM (2.5 hours)
- Good lunch break at 12:00 PM
- Free afternoon after 3:00 PM

Recommendations:
- Add 15-min buffer between 9:00 and 10:00 AM meetings
- Block 3:00 PM - 5:00 PM as focus time

Would you like me to make these changes?

### Example 2: Weekly Review with Conflicts

**User:** Show my calendar for next week

**Assistant:** Reviewing your calendar for November 11-15, 2025...

⚠️ Issues Found:

1. CONFLICT on Wednesday, Nov 13:
   - "Sprint Planning" (2:00 PM - 4:00 PM)
   - "Client Demo" (3:00 PM - 4:00 PM)
   Overlap: 1 hour

2. BACK-TO-BACK MEETINGS on Monday (4 hours straight):
   - 9:00 AM - 10:00 AM
   - 10:00 AM - 11:30 AM
   - 11:30 AM - 12:30 PM
   - 12:30 PM - 1:00 PM

3. OVER-SCHEDULED Thursday: 6 events, 7 hours of meetings

Week Summary:
- Total: 18 events
- Meeting time: 24 hours (60% of work week)
- Conflicts: 1
- Focus time: Very limited

What would you like to do?
[1] Resolve conflicts
[2] Add buffer time
[3] Decline some meetings
[4] View detailed breakdown

**User:** 1

**Assistant:** Let's resolve the Wednesday conflict...

Conflict: "Sprint Planning" vs "Client Demo" at 3:00 PM

Available options:
1. Reschedule "Client Demo" to 4:00 PM (right after Sprint Planning)
2. Reschedule "Sprint Planning" to start at 1:00 PM (ends before demo)
3. Decline "Client Demo" (if you're optional)

Which option do you prefer?

**User:** 2

**Assistant:** I'll reschedule Sprint Planning to 1:00 PM - 3:00 PM...

Updated successfully! ✓

New Wednesday schedule:
  1:00 PM - 3:00 PM: Sprint Planning
  3:00 PM - 4:00 PM: Client Demo

Conflict resolved. Would you like to address the back-to-back meetings on Monday?
