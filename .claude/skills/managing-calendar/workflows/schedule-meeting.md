# Meeting Scheduling Workflow

This prompt guides you through helping users schedule calendar events using `calendar-cli`.

## Workflow Overview

1. Gather meeting requirements from user
2. Parse and validate date/time information
3. Check for scheduling conflicts
4. Create the calendar event
5. Confirm creation and provide event details

## Step-by-Step Process

### Step 1: Gather Meeting Requirements

Collect the following information from the user (ask for missing details):

**Required:**
- **Subject/Title**: What is the meeting about?
- **Start Date/Time**: When does it start?
- **End Date/Time** OR **Duration**: When does it end / how long?

**Optional:**
- **Attendees**: Who should attend? (comma-separated email addresses)
- **Location**: Where is the meeting? (physical location or URL)
- **Body**: Additional details or agenda?
- **Calendar**: Which calendar to use? (if user has multiple)

**Example user requests:**

"Schedule a meeting with John tomorrow at 2pm for 1 hour"
- Subject: [Need to ask: "What's the meeting about?"]
- Start: tomorrow 2pm
- Duration: 1 hour
- Attendees: john@ [Need to ask: "What's John's email address?"]

"Set up a 30-minute 1:1 with alice@example.com next Monday at 10am about project status"
- Subject: 1:1 - Project Status
- Start: next Monday 10am
- Duration: 30 minutes
- Attendees: alice@example.com

### Step 2: Parse and Validate Date/Time

Convert natural language dates/times to ISO 8601 format (YYYY-MM-DDTHH:MM:SS).

**Relative date parsing:**
- "tomorrow" → calculate tomorrow's date
- "next Monday" → calculate next Monday's date
- "in 2 days" → calculate date 2 days from now
- "this Friday" → calculate this Friday's date

**Time parsing:**
- "2pm" → 14:00:00
- "10:30am" → 10:30:00
- "noon" → 12:00:00
- "end of day" → 17:00:00

**Duration calculation:**
If user provides duration instead of end time:
- "1 hour" → add 1 hour to start time
- "30 minutes" → add 30 minutes to start time
- "2 hours" → add 2 hours to start time

**Example transformations:**
```
User: "tomorrow at 2pm for 1 hour"
→ Start: 2025-11-09T14:00:00
→ End: 2025-11-09T15:00:00

User: "next Monday at 10:30am to 11:45am"
→ Start: 2025-11-11T10:30:00
→ End: 2025-11-11T11:45:00
```

**Validation:**
- Ensure end time is after start time
- Check for reasonable durations (warn if > 8 hours)
- Confirm timezone if ambiguous

Present the parsed information to user for confirmation:
```
I'll schedule the following meeting:

Subject: Team Standup
When: Tomorrow (Nov 9, 2025) 2:00 PM - 3:00 PM
Duration: 1 hour
Attendees: john@example.com, alice@example.com
Location: Conference Room A

Is this correct? [Y/N]
```

### Step 3: Check for Scheduling Conflicts

Before creating the event, check the user's calendar for conflicts.

**For single-day checks (most common):**
```bash
# Check count first
COUNT=$(calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq '.metadata.count')

# Calendar events are usually <50 per day, safe to use TOON
if [ "$COUNT" -le 50 ]; then
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --toon --fields id,subject,start.dateTime,end.dateTime
else
  # Rare case: very busy calendar, use JSON+jq
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq -r '.data[] | "\(.id)|\(.subject)|\(.start.dateTime)|\(.end.dateTime)"'
fi
```

Use the same date for both `--after` and `--before` to get events on that specific day.

Parse the response and look for overlapping events:
- Event overlaps if: (new_start < existing_end) AND (new_end > existing_start)

**If conflicts found:**
```
Warning: Scheduling conflict detected!

Your new meeting (2:00 PM - 3:00 PM) overlaps with:
- "Project Review" (1:30 PM - 2:30 PM) with bob@example.com

Would you like to:
  [1] Create anyway (double-booking)
  [2] Find alternative time
  [3] Cancel
```

**If no conflicts:**
```
Good news! No scheduling conflicts found.
```

**Finding alternative times:**
If user chooses option [2], suggest free slots:

1. Get all events for the day (use TOON if <50 events, JSON+jq if more)
2. Identify gaps between events
3. Suggest slots that fit the meeting duration

```bash
# Get day's events efficiently
COUNT=$(calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq '.metadata.count')

if [ "$COUNT" -le 50 ]; then
  # Use TOON for readable output
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --toon --fields start.dateTime,end.dateTime,subject
else
  # Use JSON+jq for large calendars
  calendar-cli find --after YYYY-MM-DD --before YYYY-MM-DD --json | jq -r '.data[] | "\(.start.dateTime) to \(.end.dateTime): \(.subject)"'
fi
```

```
Available time slots on Nov 9, 2025:
- 9:00 AM - 12:00 PM (3 hours free)
- 3:30 PM - 5:00 PM (1.5 hours free)

Your meeting needs 1 hour. Suggested times:
- 9:00 AM - 10:00 AM
- 11:00 AM - 12:00 PM
- 3:30 PM - 4:30 PM

Which would you prefer? Or specify a different time.
```

### Step 4: Create the Calendar Event

Execute the create command with all gathered information:

```bash
# Use TOON for single event creation (efficient, structured feedback)
calendar-cli create \
  --subject "Meeting Subject" \
  --start "YYYY-MM-DDTHH:MM:SS" \
  --end "YYYY-MM-DDTHH:MM:SS" \
  --attendees "email1@example.com,email2@example.com" \
  --location "Conference Room A" \
  --body "Meeting agenda and details" \
  --toon
```

**Notes:**
- Attendees must be comma-separated (no spaces)
- Location and body are optional
- Use `--calendar "Calendar Name"` if user has multiple calendars

**Handle response:**

Success:
```json
{
  "success": true,
  "data": {
    "id": "AAMkAGI...",
    "subject": "Team Standup",
    "start": "2025-11-09T14:00:00Z",
    "end": "2025-11-09T15:00:00Z",
    "attendees": [...]
  }
}
```

Extract the event ID for future reference.

Error - Validation:
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid email address in attendees"
  }
}
```

Ask user to correct the invalid information and retry.

Error - Conflict (if API enforces):
```json
{
  "success": false,
  "error": {
    "code": "CONFLICT",
    "message": "Event conflicts with existing appointment"
  }
}
```

Inform user and offer to reschedule or force create.

### Step 5: Confirm Creation and Provide Details

Present a confirmation with all event details:

```
Meeting created successfully!

Event Details:
- Subject: Team Standup
- When: Friday, November 9, 2025 @ 2:00 PM - 3:00 PM
- Duration: 1 hour
- Location: Conference Room A
- Attendees:
  • john@example.com
  • alice@example.com

Event ID: AAMkAGI...
Calendar: Calendar

Meeting invites have been sent to all attendees.

Would you like to:
- Add this meeting to your task list
- Set a reminder before the meeting
- View your full calendar for that day
- Schedule another meeting
```

## Error Handling

### Authentication Errors
```
Authentication failed. Please run: calendar-cli calendars
Follow the authentication prompt, then try again.
```

### Invalid Email Addresses
```
Error: Invalid attendee email address "[email]"
Please provide valid email addresses separated by commas.
```

### Invalid Date/Time
```
Error: Invalid date format.
Please use one of these formats:
- "tomorrow at 2pm"
- "2025-11-09 14:00"
- "next Monday at 10:30am"
```

### Past Date/Time
```
Warning: The specified time is in the past.
Did you mean:
- Tomorrow at [time]
- Next [day] at [time]
```

### Rate Limiting
```
Microsoft Graph API rate limit exceeded.
Please wait a moment before scheduling another meeting.
```

### Network Errors
```
Network connection failed. Please check your internet connection.
The meeting was not created. Would you like to retry?
```

## Advanced Scenarios

### Recurring Meetings

Note: `calendar-cli` v0.4.0 does not support recurring events yet. If user asks:

```
Recurring meetings are not yet supported by calendar-cli.

Workaround: I can create multiple individual meetings. For example:
- "Team Standup every Monday at 9am for the next 4 weeks"
→ I'll create 4 separate events on Nov 11, 18, 25, and Dec 2.

Would you like me to do this?
```

If yes, create each event individually in a loop.

### All-Day Events

For all-day events, use start/end times of 00:00:00:

```bash
calendar-cli create \
  --subject "Conference" \
  --start "2025-11-09T00:00:00" \
  --end "2025-11-10T00:00:00" \
  --json
```

### Multi-Day Events

For multi-day events, set appropriate start and end dates:

```bash
calendar-cli create \
  --subject "Company Retreat" \
  --start "2025-11-15T09:00:00" \
  --end "2025-11-17T17:00:00" \
  --location "Tahoe" \
  --json
```

### Teams/Zoom Meetings

If user wants to include a video conference link:

```bash
calendar-cli create \
  --subject "Virtual Team Meeting" \
  --start "2025-11-09T14:00:00" \
  --end "2025-11-09T15:00:00" \
  --attendees "team@example.com" \
  --location "https://zoom.us/j/123456789" \
  --body "Join Zoom Meeting: https://zoom.us/j/123456789" \
  --json
```

Note: Use `--location` for the Zoom URL so it appears prominently in calendar apps.

### Tentative vs. Confirmed

By default, events are created as "confirmed". If user wants a tentative meeting:

```
Note: calendar-cli creates meetings as confirmed by default.
If you want to mark it as tentative, I can create the meeting
and you can update the status in Outlook afterward.
```

### Optional Attendees

`calendar-cli` v0.4.0 doesn't distinguish between required and optional attendees. All attendees in `--attendees` are treated as required.

If user specifies optional attendees:
```
Note: All attendees will be marked as required.
Include them in --attendees: alice@example.com,bob@example.com

You can change their status to "optional" in Outlook after creation.
```

## Tips for Effective Scheduling

1. **Always check for conflicts** before creating events
2. **Confirm details** with user before executing create command
3. **Use clear subject lines** that indicate the meeting purpose
4. **Include location** for in-person meetings or video links for virtual ones
5. **Add agenda in body** to help attendees prepare
6. **Save event ID** in case user wants to update/cancel later
7. **Suggest buffer time** between back-to-back meetings

## Example Conversations

### Example 1: Simple Meeting

**User:** Schedule a meeting with John tomorrow at 2pm for 1 hour

**Assistant:** I'll help you schedule that. A few questions:

1. What's the meeting about? (subject)
2. What's John's full email address?

**User:** It's a project sync. john.doe@example.com

**Assistant:** Perfect! Here's what I'll schedule:

Subject: Project Sync
When: Saturday, November 9, 2025 @ 2:00 PM - 3:00 PM
Duration: 1 hour
Attendees: john.doe@example.com

Is this correct? [Y/N]

**User:** Yes

**Assistant:** Let me check for conflicts...

No scheduling conflicts found.

Creating the meeting...

Meeting created successfully!

Event Details:
- Subject: Project Sync
- When: Saturday, November 9, 2025 @ 2:00 PM - 3:00 PM
- Attendees: john.doe@example.com
- Event ID: AAMkAGI...

Meeting invite has been sent to john.doe@example.com.

### Example 2: Conflict Detected

**User:** Set up a 30-minute 1:1 with Alice next Monday at 10am

**Assistant:** I'll help schedule that. What's Alice's email address?

**User:** alice@company.com

**Assistant:** Here's what I'll schedule:

Subject: 1:1 with Alice
When: Monday, November 11, 2025 @ 10:00 AM - 10:30 AM
Attendees: alice@company.com

Checking for conflicts...

Warning: Scheduling conflict detected!

Your new meeting (10:00 AM - 10:30 AM) overlaps with:
- "Team Standup" (9:30 AM - 10:15 AM)

Would you like to:
  [1] Create anyway (15-minute overlap)
  [2] Find alternative time
  [3] Cancel

**User:** 2

**Assistant:** Available time slots on Monday, November 11:
- 10:15 AM - 12:00 PM (1.75 hours free)
- 1:00 PM - 5:00 PM (4 hours free)

Your meeting needs 30 minutes. Suggested times:
- 10:15 AM - 10:45 AM (right after Team Standup)
- 11:00 AM - 11:30 AM
- 2:00 PM - 2:30 PM

Which would you prefer?

**User:** 10:15 AM works

**Assistant:** Perfect! Creating meeting for 10:15 AM - 10:45 AM...

Meeting created successfully!

Event Details:
- Subject: 1:1 with Alice
- When: Monday, November 11, 2025 @ 10:15 AM - 10:45 AM
- Attendees: alice@company.com
- Event ID: AAMkAGI...

### Example 3: All-Day Event

**User:** Block off next Friday as PTO

**Assistant:** I'll create an all-day event for Friday, November 15, 2025.

Creating "PTO" event...

Event created successfully!

Event Details:
- Subject: PTO
- When: Friday, November 15, 2025 (All day)
- Event ID: AAMkAGI...

Your calendar is now blocked for that day.
