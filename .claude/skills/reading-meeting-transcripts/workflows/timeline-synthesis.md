# Timeline Synthesis Workflow

This prompt guides you through building a timeline of events, decisions, and key facts from meetings.

## Use Case

User wants to understand the history and evolution of a topic:
- "Help me understand the timeline of Project X decisions"
- "What were the key facts about GB200 from October to December?"
- "Summarize the history of the migration initiative"

## Current Capabilities

| Feature | Support | How |
|---------|---------|-----|
| Find topic meetings | ✅ | `calendar-cli find --subject "topic"` |
| Read transcripts | ✅ | `transcript-cli read --event-id` |
| Timeline synthesis | ✅ | Claude analyzes chronologically |
| External data | ❌ | Requires separate tools |

## Workflow Overview

1. Define topic and time range
2. Find relevant meetings
3. Read transcripts chronologically
4. Extract key facts and decisions
5. Build timeline narrative
6. Note external data needs

## Step-by-Step Process

### Step 1: Define Scope

**Clarify with user:**
```
To build your timeline, I need to understand:
1. What topic/project? (e.g., "GB200", "Migration", "Q4 Launch")
2. What time period? (e.g., "October to December 2024")
3. What should I focus on?
   - Key decisions
   - Milestones/dates
   - Problems/blockers
   - All of the above
```

**Set search parameters:**
- Topic keywords for `--subject` and `--body`
- Date range for `--after` and `--before`
- Attendees if specific people are relevant

### Step 2: Find Relevant Meetings

**Search by topic:**
```bash
# Primary: subject contains topic
calendar-cli find --subject "GB200" --after 2024-10-01 --before 2024-12-31 --toon --fields id,subject,start.dateTime,organizer.emailAddress.name

# Secondary: broader body search
calendar-cli find --body "GB200" --after 2024-10-01 --before 2024-12-31 --toon --fields id,subject,start.dateTime
```

**Filter results:**
```
Found 15 meetings related to "GB200" (Oct-Dec 2024):

1. Oct 3 - GB200 Kickoff (evt001)
2. Oct 10 - GB200 Design Review (evt002)
3. Oct 17 - GB200 Technical Deep Dive (evt003)
...
15. Dec 15 - GB200 Launch Retrospective (evt015)

Should I analyze all 15, or focus on specific ones?
```

### Step 3: Read Transcripts Chronologically

**Process meetings in date order:**

```bash
# Start with earliest meeting
transcript-cli read --event-id evt001 --toon
```

**For each meeting, extract:**
- Date and attendees
- Key topics discussed
- Decisions made
- Action items assigned
- Problems/blockers mentioned
- Milestones or deadlines

**Track availability:**
```
Transcript Status:
- ✅ Oct 3 - GB200 Kickoff - transcript available
- ❌ Oct 10 - Design Review - no transcript
- ✅ Oct 17 - Technical Deep Dive - transcript available
...
```

### Step 4: Extract Key Facts

**Build fact log as you read:**

```
## GB200 Timeline - Raw Facts

### Oct 3, 2024 - Kickoff Meeting
Source: evt001 transcript
Attendees: Alice (PM), Bob (Eng Lead), Carol (Design)

Facts:
- Project officially started
- Target launch: December 1, 2024
- Budget approved: $500K
- Bob assigned as technical lead

Decisions:
- Use microservices architecture
- Weekly standups starting next week

---

### Oct 17, 2024 - Technical Deep Dive
Source: evt003 transcript
Attendees: Bob, Dave, Eve (all engineering)

Facts:
- Architecture diagram finalized
- 3 major components identified
- Performance requirements: 100ms latency

Decisions:
- Use AWS Lambda for compute
- PostgreSQL for data layer

Blockers mentioned:
- Dependency on Auth team for OAuth integration

---

[Continue for each meeting...]
```

### Step 5: Build Timeline Narrative

**Synthesize into coherent timeline:**

```
# GB200 Project Timeline (October - December 2024)

## Executive Summary
The GB200 project progressed from kickoff to launch over 3 months, with
one major delay in November due to authentication dependencies.

## Timeline

### Phase 1: Initiation (October 1-15)

**Oct 3 - Project Kickoff**
- Project officially launched with $500K budget
- Bob appointed technical lead
- Target launch: December 1, 2024
- Key decision: Microservices architecture chosen

**Oct 10 - Design Review**
- [No transcript available - meeting held but not recorded]

**Oct 17 - Technical Deep Dive**
- Architecture finalized
- Performance target set: 100ms latency
- ⚠️ Dependency identified: OAuth integration blocked on Auth team

---

### Phase 2: Development (October 16 - November 30)

**Oct 24 - Sprint 1 Review**
- Core services 40% complete
- On track for December launch

**Nov 7 - Sprint 2 Review**
- ⚠️ BLOCKER: Auth team delayed, OAuth integration slipping
- Risk to December launch identified

**Nov 14 - Escalation Meeting**
- Launch date pushed to December 15
- Decision: Proceed with basic auth as fallback

**Nov 28 - Sprint 4 Review**
- OAuth integration complete
- Performance testing started

---

### Phase 3: Launch (December 1-15)

**Dec 5 - Pre-Launch Review**
- All features complete
- Go/no-go decision: GO
- Launch date confirmed: December 10

**Dec 10 - Launch Day**
- Successful deployment at 10 AM PST
- Minor issues resolved by EOD

**Dec 15 - Retrospective**
- Project deemed successful
- Lessons learned documented

---

## Key Milestones

| Date | Milestone | Notes |
|------|-----------|-------|
| Oct 3 | Project kickoff | Budget approved |
| Oct 17 | Architecture finalized | Microservices |
| Nov 14 | Launch date slipped | OAuth dependency |
| Dec 10 | Successful launch | On revised schedule |

## Key Decisions

1. **Oct 3**: Microservices architecture selected
2. **Oct 17**: AWS Lambda + PostgreSQL stack
3. **Nov 14**: Launch postponed 2 weeks, basic auth fallback

## Open Items / Blockers History

| Issue | Raised | Resolved | Resolution |
|-------|--------|----------|------------|
| OAuth integration | Oct 17 | Nov 28 | Auth team delivered |
| Performance concerns | Nov 7 | Nov 28 | Passed testing |

## Notes
- Transcripts available for 12/15 meetings
- 3 meetings had no recordings
```

### Step 6: External Data Correlation

**Acknowledge limitations:**
```
## External Data References

This timeline is based on meeting transcripts only. You mentioned wanting
to correlate with:
- Kratos metrics
- Grafana dashboards

To include external data:
1. Retrieve data from those systems separately
2. Provide the data to me
3. I can then correlate with timeline events

For example:
- "Here are the Grafana metrics from Oct-Dec" → I'll match to timeline
- "Here's the Jira ticket history" → I'll correlate issues with meetings
```

## Example Session

**User:** "Build a timeline of the migration project from Q4"

**Claude's process:**

1. **Clarify:**
   ```
   I'll search for migration-related meetings from October-December 2024.
   What aspects should I focus on?
   - Technical decisions
   - Timeline changes
   - Blockers and resolutions
   - All of the above
   ```

2. **Search:**
   ```bash
   calendar-cli find --subject "migration" --after 2024-10-01 --before 2024-12-31 --toon --fields id,subject,start.dateTime
   ```

3. **Read transcripts chronologically:**
   ```bash
   transcript-cli read --event-id <earliest-meeting> --toon
   # ... continue in date order
   ```

4. **Present timeline:**
   ```
   # Migration Project Timeline (Q4 2024)
   
   ## Summary
   Database migration project spanning 3 months...
   
   ## Key Events
   Oct 5 - Migration planning kickoff
   Oct 19 - Schema design approved
   Nov 2 - Development started
   Nov 16 - First staging deployment
   Dec 7 - Production cutover (successful)
   
   [Full detailed timeline...]
   ```

## Tips and Limitations

**Tips:**
- Process meetings chronologically for coherent narrative
- Note when transcripts are unavailable
- Flag unresolved items separately from resolved ones
- Use tables for quick reference (milestones, decisions)

**Limitations:**
- Cannot access external systems (Kratos, Grafana, Jira)
- Some meetings may not have transcripts
- Email discussions may contain additional context (search separately)
- Verbal side-conversations not captured

**Multi-source correlation:**
If user has external data, they can provide it for Claude to correlate:
```
"Here are the deployment dates from our CI/CD system..."
"Here's the Jira ticket history for this project..."
```

## Related Workflows

- [transcript-workflow.md](transcript-workflow.md) - Reading transcripts
- [evidence-review.md](evidence-review.md) - Fact verification
- [search-emails.md](search-emails.md) - Finding related emails
