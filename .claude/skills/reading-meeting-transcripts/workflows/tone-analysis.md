# Tone & Sentiment Analysis Workflow

This prompt guides you through analyzing communication tone and patterns from meeting transcripts.

## Use Case

User wants self-reflection on their communication style:
- "Review the tone of my last 10 meetings"
- "How am I showing up to my team lately?"
- "I've been stressed—is it affecting my communication?"

## Current Capabilities

| Feature | Support | How |
|---------|---------|-----|
| Find recent meetings | ✅ | `calendar-cli find` |
| Read transcripts | ✅ | `transcript-cli read` with speaker labels |
| Filter to user's speech | ⚠️ | Manual extraction using speaker labels |
| Analyze tone | ✅ | Claude analyzes extracted text |

## Workflow Overview

1. Clarify scope and context
2. Find recent meetings with transcripts
3. Read and extract user's statements
4. Analyze tone patterns
5. Provide insights with examples

## Step-by-Step Process

### Step 1: Clarify Scope and Context

**Understand what user wants to know:**
```
To analyze your communication tone, I need to understand:

1. How many meetings to review? (e.g., last 10, past 2 weeks)
2. What's your name as it appears in transcripts?
3. Any specific concerns? (e.g., "I've been irritable", "too direct")
4. What kind of feedback would be helpful?
   - General tone patterns
   - Specific examples to review
   - Comparison over time
   - Actionable suggestions
```

**Set expectations:**
```
I'll look at your statements in meeting transcripts and analyze:
- Overall tone (collaborative, directive, supportive, terse)
- Language patterns
- Specific examples with context
- Changes across meetings (if multiple)

Note: Tone analysis is subjective. I'll provide observations, not judgments.
Context matters—a curt response may be appropriate in some situations.
```

### Step 2: Find Recent Meetings

**Calculate date range:**
```bash
# For "last 10 meetings" - search broadly, then limit
calendar-cli find --after 2025-01-01 --limit 20 --toon --fields id,subject,start.dateTime

# For "past 2 weeks"
calendar-cli find --after 2025-01-17 --toon --fields id,subject,start.dateTime
```

**Filter to attended meetings:**
```
Found 15 meetings in the date range.
Checking which have transcripts...
```

### Step 3: Read and Extract User's Statements

**For each meeting with a transcript:**

```bash
transcript-cli read --event-id <event-id> --toon
```

**Extract user's statements:**

Look for the user's name in speaker labels and collect their statements:

```
Meeting: Team Standup (Jan 20)
---
User's statements extracted:

[00:02:15] "Let's get started. Sarah, what's your status?"
[00:05:30] "Can you clarify what you mean by 'blocked'?"
[00:08:45] "We need to move faster on this."
[00:12:10] "Good work. Let's make sure we stay on track."
[00:15:00] "Any other blockers? Okay, thanks everyone."

---
```

**Build a collection across meetings:**

```
## Statement Collection

### Meeting 1: Team Standup (Jan 20)
Statements: 5
Key quotes:
- "We need to move faster on this."
- "Can you clarify what you mean by 'blocked'?"

### Meeting 2: Project Review (Jan 21)
Statements: 12
Key quotes:
- "I disagree with that approach."
- "Have we considered the alternative?"
- "Let's take this offline."

### Meeting 3: 1:1 with Sarah (Jan 22)
Statements: 18
Key quotes:
- "I appreciate you raising this."
- "What support do you need from me?"
- "That's a good point, I hadn't considered that."

[Continue for all meetings...]
```

### Step 4: Analyze Tone Patterns

**Tone indicators to look for:**

| Indicator | Positive | Concerning |
|-----------|----------|------------|
| Questions | Open, curious | Interrogating, challenging |
| Directives | Clear, actionable | Curt, demanding |
| Acknowledgment | "Good point", "I appreciate" | Absent or minimal |
| Pronouns | "We", "us" (collaborative) | "I", "you" (distancing) |
| Qualifiers | "I think", "perhaps" | Absent (absolute statements) |
| Interruptions | N/A | Cutting others off |

**Analyze across meetings:**

```
## Tone Analysis: [User Name]
Period: Jan 20-27, 2025 (7 meetings analyzed)

### Overall Tone Profile

**Dominant style:** Direct and task-focused
- Tends toward directive statements
- Questions often seeking clarification rather than exploring
- Acknowledgment present but brief

### Specific Observations

**Positive patterns:**
1. Clear communication - statements are unambiguous
2. Some acknowledgment present ("Good work", "I appreciate")
3. 1:1 meetings show more warmth than group settings

**Areas of concern:**
1. Group meetings show more terse responses
2. Limited exploration questions ("Have we considered...")
3. Some interruption patterns noted (Jan 21 meeting)

### Examples with Context

**Example 1: Direct but potentially abrupt**
Meeting: Team Standup (Jan 20)
Statement: "We need to move faster on this."
Context: Team was discussing timeline
Observation: Direct, but lacks collaborative framing. Compare: "How can we accelerate this?"

**Example 2: Good acknowledgment**
Meeting: 1:1 with Sarah (Jan 22)
Statement: "I appreciate you raising this. What support do you need?"
Observation: Supportive, collaborative, invites dialogue

**Example 3: Challenging tone**
Meeting: Project Review (Jan 21)
Statement: "I disagree with that approach." [interrupted speaker]
Observation: Disagreement is fine, but interruption added edge
```

### Step 5: Provide Insights

**Present findings thoughtfully:**

```
## Communication Insights for [User Name]

### Summary

Based on 7 meetings over the past week, your communication style is 
**direct and task-oriented**. This can be effective for driving results,
but some patterns suggest room for more collaborative engagement.

### What's Working

✓ Clear, unambiguous communication
✓ Effective in 1:1 settings - more warmth and curiosity
✓ Acknowledgment present when things go well

### Potential Growth Areas

⚠️ Group meetings show more terseness than 1:1s
⚠️ Questions tend toward clarification vs. exploration
⚠️ Some instances of interrupting or cutting off discussion

### Specific Recommendations

1. **In group meetings:** Try adding a collaborative frame
   - Instead of: "We need to move faster"
   - Try: "How can we accelerate this? What's getting in the way?"

2. **When disagreeing:** Pause before responding
   - Instead of: "I disagree with that approach" [immediately]
   - Try: "Help me understand your thinking on that"

3. **Acknowledgment:** More explicit recognition
   - Current: "Good work"
   - Enhanced: "Good work on the timeline adjustment, that was exactly what we needed"

### Context Matters

Your directness may be entirely appropriate for:
- Urgent situations requiring quick decisions
- Team members who prefer direct feedback
- Meetings running over time

The examples flagged aren't "wrong" - they're patterns to be aware of
so you can choose when directness serves you and when warmth might work better.
```

## Example Session

**User:** "I've been stressed lately. Can you review how I'm coming across in meetings?"

**Claude's process:**

1. **Clarify:**
   ```
   I'll look at your recent meeting transcripts to analyze your communication tone.
   - How many meetings should I review? (I'd suggest 5-10 for patterns)
   - What's your name as it appears in Teams? (e.g., "John Smith")
   - Any specific concerns you want me to watch for?
   ```

2. **Find meetings:**
   ```bash
   calendar-cli find --after 2025-01-20 --toon --fields id,subject,start.dateTime
   ```

3. **Read transcripts and extract statements:**
   ```bash
   transcript-cli read --event-id evt001 --toon
   # Extract user's statements from speaker labels
   ```

4. **Analyze and present:**
   ```
   ## Tone Review: Last 5 Meetings
   
   I found some patterns worth noting:
   
   **Positive observations:**
   - You're still acknowledging team contributions
   - 1:1 meetings show warmth and support
   
   **Stress indicators I noticed:**
   - Shorter responses in group meetings (averaging 8 words vs your typical 15)
   - More directive statements ("Just do X") vs collaborative ("How should we X?")
   - One instance of a curt response: "That's already been discussed" (Jan 23)
   
   **Specific example:**
   Jan 23 Standup - when Bob asked about timeline:
   Your response: "We covered that yesterday. Next item."
   
   This might have come across as dismissive. You may have been efficient,
   or you may have been shorter than intended.
   
   Would you like me to go deeper on any particular meeting?
   ```

## Tips and Limitations

**Tips:**
- Review 5-10 meetings for meaningful patterns
- Compare 1:1s vs group meetings (often different dynamics)
- Look for changes over time if analyzing stress
- Provide specific examples, not just labels

**Limitations:**
- Tone in text is subjective—lacks vocal tone, facial expressions
- Context matters (urgency, relationship, meeting purpose)
- Transcription may miss nuance (interruptions, pauses)
- Cannot filter to specific speaker (manual extraction required)

**Sensitivity:**
- Frame observations non-judgmentally
- Acknowledge that directness isn't inherently bad
- Provide actionable suggestions if requested
- Recognize context affects interpretation

## Related Workflows

- [meeting-analytics.md](meeting-analytics.md) - Participation patterns
- [transcript-workflow.md](transcript-workflow.md) - Reading transcripts
- [timeline-synthesis.md](timeline-synthesis.md) - Changes over time
