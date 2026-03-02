# Meeting Participation Analytics Workflow

This prompt guides you through analyzing meeting participation patterns using transcripts.

## Use Case

User wants to understand meeting dynamics:
- "Who participates most in our team meetings?"
- "Who tends to lead discussions?"
- "Help me understand participation patterns for project X meetings"

## Current Capabilities

| Feature | Support | How |
|---------|---------|-----|
| Find meetings | ✅ | `calendar-cli find` |
| Read transcripts | ✅ | `transcript-cli read` with speaker labels |
| Manual analysis | ✅ | Claude analyzes transcript content |
| Automated stats | ❌ | No built-in aggregation |

## Workflow Overview

1. Identify meetings to analyze
2. Retrieve transcripts
3. Extract speaker patterns
4. Synthesize findings
5. Present insights

## Step-by-Step Process

### Step 1: Identify Meetings to Analyze

**Find relevant meetings:**
```bash
# All meetings in date range
calendar-cli find --after 2025-01-01 --before 2025-02-01 --toon --fields id,subject,start.dateTime,organizer.emailAddress.name

# Meetings with specific attendee
calendar-cli find --attendee "colleague@company.com" --after 2025-01-01 --toon --fields id,subject,start.dateTime

# Meetings by subject/series
calendar-cli find --subject "Team Standup" --after 2025-01-01 --toon --fields id,subject,start.dateTime
```

**Select meetings for analysis:**
```
Found 12 meetings matching criteria:

1. Team Standup (Jan 6) - ID: evt001
2. Team Standup (Jan 13) - ID: evt002
3. Project Review (Jan 8) - ID: evt003
...

Which meetings would you like me to analyze? (Enter numbers, "all", or "first 5")
```

### Step 2: Retrieve Transcripts

**For each selected meeting:**
```bash
# Read transcript directly from event ID
transcript-cli read --event-id <event-id> --toon
```

**If no transcript available:**
```
Meeting "Team Standup (Jan 6)" - No transcript available.
This meeting may not have been recorded, or the transcript hasn't been processed yet.
```

**Track transcript availability:**
```
Transcript Status:
- ✅ Team Standup (Jan 6) - 45 min transcript
- ✅ Team Standup (Jan 13) - 38 min transcript
- ❌ Project Review (Jan 8) - No transcript
- ✅ Team Standup (Jan 20) - 42 min transcript
...

Proceeding with 3 available transcripts.
```

### Step 3: Extract Speaker Patterns

**For each transcript, track:**

1. **Speaker frequency** - Who speaks and how often
2. **Discussion initiation** - Who starts new topics
3. **Question ratio** - Questions asked vs statements made
4. **Response patterns** - Who responds to whom

**Manual tracking approach:**

As you read each transcript, build a speaker profile:

```
Meeting: Team Standup (Jan 6)
Duration: 45 minutes

Speaker Analysis:
┌─────────────────┬──────────┬───────────┬───────────┬─────────────┐
│ Speaker         │ Entries  │ Questions │ Statements│ First/Last  │
├─────────────────┼──────────┼───────────┼───────────┼─────────────┤
│ Alice (PM)      │ 23       │ 8         │ 15        │ First       │
│ Bob (Eng)       │ 15       │ 2         │ 13        │             │
│ Carol (Eng)     │ 12       │ 1         │ 11        │             │
│ Dave (Design)   │ 8        │ 3         │ 5         │ Last        │
└─────────────────┴──────────┴───────────┴───────────┴─────────────┘

Observations:
- Alice leads the meeting (speaks first, most entries, most questions)
- Bob and Carol contribute technical updates
- Dave participates less frequently
```

**Track commitments made:**
```
Commitments Noted:
- Bob: "I'll have the PR ready by Thursday"
- Carol: "I'll review the design doc tomorrow"
- Alice: "I'll schedule the stakeholder demo"
```

### Step 4: Synthesize Findings

**Aggregate across meetings:**

After analyzing all transcripts, combine findings:

```
## Participation Analysis: Team Standups (Jan 2025)

### Overall Participation Ranking

| Speaker | Total Entries | Avg/Meeting | Questions | Commitments |
|---------|---------------|-------------|-----------|-------------|
| Alice   | 68            | 23          | 24 (35%)  | 4           |
| Bob     | 45            | 15          | 6 (13%)   | 8           |
| Carol   | 38            | 13          | 4 (11%)   | 6           |
| Dave    | 22            | 7           | 5 (23%)   | 2           |

### Participation Patterns

**Discussion Leaders:**
- Alice consistently leads (speaks first in 3/3 meetings)
- Asks most questions, drives agenda

**Active Contributors:**
- Bob and Carol have similar participation levels
- Focus on status updates (lower question ratio)

**Less Active:**
- Dave participates least but asks proportionally more questions
- May be listening more than speaking

### Commitment Tracking

| Person | Commitments Made | Notes |
|--------|------------------|-------|
| Bob    | 8                | Most action items taken |
| Carol  | 6                | Technical tasks |
| Alice  | 4                | Coordination/scheduling |
| Dave   | 2                | Design-related |
```

### Step 5: Present Insights

**Provide actionable insights:**

```
## Key Insights

**Meeting Dynamics:**
- Alice (PM) drives discussions through questions and agenda management
- Engineering (Bob, Carol) provide updates; Design (Dave) less verbal
- Meetings average 40 minutes with 4 active speakers

**Participation Balance:**
- Top 2 speakers account for ~65% of discussion
- Consider: Should quieter team members have more structured speaking time?

**Follow-up Actions:**
- Dave's participation is low—consider if design topics are adequately covered
- Bob takes most action items—monitor for overload

**Limitations:**
- Analysis based on 3 meetings; patterns may shift with more data
- Speaking frequency ≠ contribution value
- Cannot track outcomes of commitments from transcripts alone
```

## Example Session

**User:** "Who participates most in our weekly design reviews?"

**Claude's process:**

1. **Find meetings:**
   ```bash
   calendar-cli find --subject "Design Review" --after 2025-01-01 --toon --fields id,subject,start.dateTime
   ```

2. **Read transcripts:**
   ```bash
   transcript-cli read --event-id <event-id-1> --toon
   transcript-cli read --event-id <event-id-2> --toon
   ```

3. **Analyze each transcript**, tracking speakers

4. **Present findings:**
   ```
   Based on the last 4 Design Review meetings:
   
   Top Participants:
   1. Sarah (Design Lead) - 45% of discussion, leads all meetings
   2. Mike (Senior Designer) - 25% of discussion
   3. Product reps - 20% combined
   4. Engineering reps - 10% combined
   
   Pattern: Design team dominates discussion; consider if cross-functional
   input is sufficient.
   ```

## Tips and Limitations

**Tips:**
- Start with a small number of meetings (3-5) for initial analysis
- Focus on recurring meetings for consistent comparison
- Track commitments to correlate with follow-through later
- Use speaker names as they appear in transcripts (may be display names)

**Limitations:**
- Manual analysis is tedious for many meetings
- No automated speaker statistics (yet)
- Cannot track commitment outcomes automatically
- Speaking frequency doesn't measure contribution quality
- Some speakers may contribute more via chat (not captured)

**Say/Do Ratio Analysis:**
To track "say/do ratio" (commitments vs. outcomes):
1. Extract commitments from transcripts
2. Check follow-up meetings for completion mentions
3. Review emails for status updates
4. This requires manual correlation across multiple data sources

## Future Enhancements

When available, these CLI features will make analytics easier:
- `transcript-cli stats --speaker` - Automated speaker statistics
- `transcript-cli actions` - Action item extraction
- Cross-meeting aggregation support

## Related Workflows

- [transcript-workflow.md](transcript-workflow.md) - Reading individual transcripts
- [calendar-review.md](calendar-review.md) - Calendar analysis
- [open-loops-review.md](open-loops-review.md) - Tracking pending items
