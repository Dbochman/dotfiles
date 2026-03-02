# Meeting Transcript Workflow

This prompt guides you through helping users access and analyze Teams meeting transcripts using `transcript-cli`.

## Workflow Overview

1. Find the calendar event
2. Read the transcript (one command!)
3. Analyze or summarize the discussion
4. Extract action items and decisions

## Quick Start: The Simple Path

**Most common case - read a transcript from a calendar event:**

```bash
# 1. Find the meeting
calendar-cli find --after 2025-12-10 --subject "Team Sync" --toon

# 2. Read the transcript directly
transcript-cli read --event-id <event-id> --toon
```

That's it! Just 2 commands.

## Step-by-Step Process

### Step 1: Find the Meeting

Help the user identify the meeting they want:

```bash
# Find recent meetings
calendar-cli find --after 2025-12-10 --toon

# Find by subject
calendar-cli find --after 2025-12-10 --subject "standup" --toon

# Find by organizer
calendar-cli find --after 2025-12-10 --organizer boss@company.com --toon
```

**Prompt for User:**
```
To find a meeting transcript, I need to identify the meeting.

Tell me:
- The meeting subject (or part of it)
- Approximate date (today, yesterday, last week, etc.)

Or paste the event ID if you have it.
```

### Step 2: Read the Transcript

Once you have the event ID, read the transcript directly:

```bash
# Read transcript from calendar event (simplest!)
transcript-cli read --event-id <event-id> --toon
```

**If the meeting has multiple transcripts** (e.g., recurring meetings with multiple recordings):

```bash
# List available transcripts first
transcript-cli find --event-id <event-id> --toon

# Read a specific one (0 = most recent, 1 = second most recent)
transcript-cli read --event-id <event-id> --transcript-index 1 --toon
```

**Output Format:**
```
Fetching transcript content...

[00:00:01] John Doe: Hello everyone, welcome to the weekly sync.
[00:00:06] Jane Smith: Thanks for having us. Let's get started.
[00:00:11] John Doe: First item on the agenda is the Q4 roadmap.
                     I've shared the document in the chat.
[00:00:25] Jane Smith: I reviewed it. I have a few questions about the timeline.
```

Note: When the same speaker speaks consecutively, their name is omitted and text is indented for readability.

### Step 3: Offer Analysis

After reading the transcript, offer analysis options:

```
I've retrieved the transcript for "Weekly Team Sync" (30 minutes, 4 participants).

What would you like me to do?

[1] Summarize the meeting
    → Key discussion points and outcomes

[2] Extract action items
    → Tasks assigned with owners and deadlines

[3] Extract decisions
    → Key decisions made during the meeting

[4] Search for specific topics
    → Find discussions about a particular subject

[5] Export transcript
    → Save to a file for external processing

[6] Nothing more
    → View the raw transcript only
```

### Step 4: Provide Analysis

Based on user selection, analyze the transcript content:

#### Meeting Summary

```
Meeting Summary: Weekly Team Sync
Date: December 16, 2025
Duration: 30 minutes
Participants: John Doe, Jane Smith, Bob Wilson, Carol Davis

Key Discussion Points:
1. Q4 Roadmap Review
   - Team reviewed the proposed Q4 roadmap
   - Timeline concerns raised about API integration
   - Agreed to extend API phase by 2 weeks

2. Sprint Progress
   - Current sprint 80% complete
   - Blocking issue with authentication fixed
   - Demo scheduled for Friday

3. Resource Allocation
   - Two new team members joining next week
   - Onboarding plan discussed

Overall Tone: Productive, collaborative
Follow-up Meeting: Friday at 2 PM for demo
```

#### Action Items

```
Action Items from "Weekly Team Sync":

1. [ ] Update Q4 roadmap with revised API timeline
   Owner: John Doe
   Due: December 18, 2025
   Context: "I'll update the roadmap by Wednesday"

2. [ ] Prepare demo environment for Friday
   Owner: Bob Wilson
   Due: December 20, 2025
   Context: "I'll have the staging environment ready for the demo"

3. [ ] Create onboarding tasks for new team members
   Owner: Jane Smith
   Due: December 17, 2025
   Context: "I'll set up their access and initial tasks"

Total: 3 action items across 3 owners
```

#### Key Decisions

```
Decisions Made in "Weekly Team Sync":

1. API Integration Timeline Extended
   - Original: December 30, 2025
   - New: January 13, 2026
   - Reason: Technical complexity higher than estimated
   - Approved by: All attendees

2. Demo Date Confirmed
   - Date: Friday, December 20, 2025
   - Time: 2:00 PM PST
   - Attendees: Full team + stakeholders
```

## Common Workflows

### Workflow 1: Yesterday's Meeting Transcript

```
User: Get me the transcript from yesterday's team standup

Steps:
1. Find yesterday's standup:
   calendar-cli find --after 2025-12-16 --before 2025-12-17 --subject "standup" --toon

2. Read transcript:
   transcript-cli read --event-id <event-id> --toon

3. Summarize or analyze as needed
```

### Workflow 2: Find What Was Discussed About Topic

```
User: What did we discuss about the budget in last week's planning meeting?

Steps:
1. Find the planning meeting:
   calendar-cli find --after 2025-12-09 --before 2025-12-16 --subject "planning" --toon

2. Read transcript:
   transcript-cli read --event-id <event-id> --toon

3. Search for "budget" in transcript content

4. Present relevant excerpts with timestamps and speakers
```

### Workflow 3: Export Meeting Notes

```
User: Export the transcript from today's client call as a file

Steps:
1. Find the meeting:
   calendar-cli find --after 2025-12-17 --subject "client" --toon

2. Export raw VTT:
   transcript-cli read --event-id <event-id> --raw > client-call-2025-12-17.vtt

3. Confirm file created
```

### Workflow 4: Read Specific Transcript When Multiple Exist

```
User: Get the transcript from the second occurrence of our weekly sync

Steps:
1. Find available transcripts:
   transcript-cli find --event-id <event-id> --toon

2. Read specific transcript by index:
   transcript-cli read --event-id <event-id> --transcript-index 1 --toon
```

## Raw VTT Export

For external processing or archival:

```bash
# Export raw VTT format
transcript-cli read --event-id <event-id> --raw > meeting-transcript.vtt
```

**VTT Format:**
```
WEBVTT

00:00:01.000 --> 00:00:05.000
<v John Doe>Hello everyone, welcome to the weekly sync.</v>

00:00:06.000 --> 00:00:10.000
<v Jane Smith>Thanks for having us. Let's get started.</v>
```

## Error Handling

### No Transcript Available

```
This meeting doesn't have a transcript available.

This can happen when:
- Transcription wasn't enabled during the meeting
- The transcript is still being processed (wait a few minutes)
- The meeting was too short or had no audio

Would you like me to search for a different meeting?
```

### Event Has No Online Meeting

```
This calendar event doesn't have a Teams meeting associated with it.

Only Teams meetings can have transcripts. This appears to be:
- An in-person meeting, or
- A meeting without a Teams link

Would you like me to search for a different meeting?
```

### Permission Denied

```
You don't have permission to access this transcript.

You can access transcripts for:
- Meetings you organized
- Meetings you attended

This meeting was organized by someone else and you weren't listed as an attendee.
```

## Tips for Effective Transcript Use

1. **Use event IDs**: Always use `--event-id` when you have a calendar event - it's the simplest approach
2. **Allow processing time**: Transcripts may take 5-10 minutes to appear after a meeting ends
3. **Use raw export for archival**: VTT format preserves all timing and speaker information
4. **Search before summarizing**: If looking for specific topics, search first to reduce analysis scope
5. **Handle multiple transcripts**: Use `--transcript-index` when meetings have multiple recordings

## Example Conversations

### Example 1: Quick Transcript Access

**User:** Get me the transcript from today's standup

**Assistant:** Let me find today's standup in your calendar...

```bash
calendar-cli find --after 2025-12-17 --subject "standup" --toon
```

Found "Daily Standup" at 9:00 AM today.

Reading transcript...

```bash
transcript-cli read --event-id AAMkAGI... --toon
```

[09:00:15] Alice Chen: Good morning everyone. Let's start with updates.
[09:00:25] Bob Kim: I finished the API refactoring yesterday.
                    Today I'm working on the test coverage.
[09:00:45] Carol Lee: I'm blocked on the design review.
                      Waiting for feedback from the design team.
[09:01:10] Alice Chen: Let me follow up with them today.
                       Anything else?
[09:01:25] Bob Kim: Nothing from me.
[09:01:30] Carol Lee: All good.
[09:01:35] Alice Chen: Great, talk tomorrow!

---
Duration: ~2 minutes
Participants: Alice Chen, Bob Kim, Carol Lee

Would you like me to summarize the key points or extract action items?

### Example 2: Finding Specific Discussion

**User:** What did John say about the deadline in yesterday's project meeting?

**Assistant:** Let me find yesterday's project meeting and search for deadline discussions...

```bash
calendar-cli find --after 2025-12-16 --before 2025-12-17 --subject "project" --toon
```

Found "Project Status" meeting from yesterday at 2:00 PM.

Reading transcript...

```bash
transcript-cli read --event-id AAMkAGI... --toon
```

Searching for "deadline"...

Found 3 mentions:

[14:05:23] John Smith: The current deadline is December 31st.
[14:05:45] John Smith: Given the scope changes, we might need to extend the deadline.
                       I suggest January 15th as a more realistic target.
[14:12:30] John Smith: Let's confirm the deadline change with stakeholders by Friday.

Summary: John discussed extending the deadline from December 31st to January 15th due to scope changes, and proposed confirming this with stakeholders by Friday.

Would you like the full context around these mentions?

### Example 3: Export for External Use

**User:** Save the transcript from this morning's client call

**Assistant:** Let me find this morning's client call...

```bash
calendar-cli find --after 2025-12-17 --subject "client" --toon
```

Found "Client Sync - Acme Corp" at 10:00 AM.

Exporting transcript...

```bash
transcript-cli read --event-id AAMkAGI... --raw > client-sync-acme-2025-12-17.vtt
```

Transcript exported to `client-sync-acme-2025-12-17.vtt`

The file contains the full transcript in WebVTT format with timestamps and speaker labels.
