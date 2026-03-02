# Evidence Review / Fact-Checking Workflow

This prompt guides you through using email and calendar as evidence sources for verification tasks.

## Use Case

User wants to verify claims, check historical records, or investigate past communications:
- "I was told there were no issues with project X. Can you check my email for contradictions?"
- "Did we ever discuss this topic with the vendor?"
- "What was the timeline of decisions on this initiative?"

## Workflow Overview

1. Clarify the claim and scope
2. Identify search terms
3. Search systematically
4. Analyze and categorize findings
5. Report with evidence citations

## Step-by-Step Process

### Step 1: Clarify the Claim and Scope

**Essential questions to ask:**

1. **What specific claim is being verified?**
   - Get the exact statement being checked
   - Identify what would prove or disprove it

2. **What is the relevant time period?**
   - "During Q3 2025" → `--after 2025-07-01 --before 2025-10-01`
   - "Last month" → calculate date range
   - "Around the time of the launch" → ask for approximate dates

3. **Who are the relevant parties?**
   - Project stakeholders
   - People who made the claim
   - People who might have raised issues

4. **What constitutes evidence?**
   - Direct statements vs. implied concerns
   - Formal communications vs. casual mentions

**Present understanding to user:**
```
I'll search for evidence regarding:
- Claim: "[specific claim]"
- Time period: [date range]
- Focus areas: [project name, key people]
- Looking for: [what constitutes contradiction/confirmation]

Is this correct?
```

### Step 2: Identify Search Terms

**Primary searches (directly related):**
```bash
# Project or topic name
outlook-cli find --subject "[project name]" --after 2025-07-01 --before 2025-10-01 --toon --fields id,subject,from.emailAddress.address,receivedDateTime

# Key people involved
outlook-cli find --from "stakeholder@company.com" --after 2025-07-01 --before 2025-10-01 --toon --fields id,subject,receivedDateTime
```

**Secondary searches (issue indicators):**
```bash
# Problem-related keywords
outlook-cli find --body "issue" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
outlook-cli find --body "problem" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
outlook-cli find --body "concern" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
outlook-cli find --body "blocker" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
outlook-cli find --body "delay" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
outlook-cli find --body "risk" --subject "[project]" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

**Expand search if needed:**
```bash
# Broader body search
outlook-cli find --body "[project name]" --after 2025-07-01 --before 2025-10-01 --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

### Step 3: Search Systematically

**Execute searches and track results:**

```bash
# Primary: Project name in subject
outlook-cli find --subject "Project X" --after 2025-07-01 --before 2025-10-01 --toon
```

**For each search, note:**
- Total results found
- Email IDs of potentially relevant messages
- Initial assessment from subject lines

**Create a candidate list:**
```
Search Results Summary:
- "Project X" in subject: 23 emails found
- From project lead: 8 emails
- "Issue" + "Project X": 3 emails ⚠️
- "Problem" + "Project X": 1 email ⚠️
- "Delay" + "Project X": 2 emails ⚠️

Flagged for detailed review: 6 emails with issue-related keywords
```

### Step 4: Analyze and Categorize Findings

**Read flagged emails:**
```bash
outlook-cli read <message-id> --toon
```

**For each email, categorize:**

| Category | Description | Weight |
|----------|-------------|--------|
| **Contradicts** | Directly mentions issues, problems, or concerns | Strong evidence |
| **Suggests issues** | Implies difficulties without explicit statement | Moderate evidence |
| **Neutral** | Mentions project but no issue indicators | No evidence either way |
| **Supports claim** | Explicitly states things are going well | Evidence for claim |

**Build evidence table:**
```
Evidence Analysis:

CONTRADICTING EVIDENCE:
1. Email ID: ABC123 (2025-08-15)
   From: engineer@company.com
   Subject: "Re: Project X Status"
   Quote: "We're seeing significant performance issues that need to be addressed before launch"
   Category: CONTRADICTS claim of no issues

2. Email ID: DEF456 (2025-08-22)
   From: pm@company.com
   Subject: "Project X - Blockers"
   Quote: "Three blockers identified this sprint..."
   Category: CONTRADICTS claim of no issues

SUPPORTING EVIDENCE:
3. Email ID: GHI789 (2025-09-01)
   From: director@company.com
   Subject: "Project X Launch Approval"
   Quote: "All issues resolved, clear to proceed"
   Category: SUPPORTS claim (but dated after earlier issues)

NEUTRAL:
4. Email ID: JKL012 (2025-08-10)
   Subject: "Project X Weekly Update"
   Content: Routine status update, no issues mentioned
```

### Step 5: Report with Evidence Citations

**Present findings clearly:**

```
## Evidence Review: "[Original Claim]"

### Summary
Based on [N] relevant emails from [date range], I found evidence that
[supports/contradicts/is inconclusive regarding] the claim.

### Key Findings

**Contradicting Evidence (N emails):**
The following emails indicate issues were present during this period:

1. **Aug 15, 2025** - engineer@company.com
   "[Quote from email]"
   Message ID: ABC123

2. **Aug 22, 2025** - pm@company.com
   "[Quote from email]"
   Message ID: DEF456

**Supporting Evidence (N emails):**
[If any emails support the claim]

**Timeline:**
- Aug 15: First issue mentioned
- Aug 22: Blockers documented
- Sep 01: Issues reported resolved

### Confidence Level
[HIGH/MEDIUM/LOW] - Based on [number of sources, directness of evidence]

### Caveats
- This review covers email only; issues may have been discussed in:
  - Meetings (check calendar/transcripts)
  - Chat platforms (not accessible via this tool)
  - Verbal conversations
- Email may not capture all communications on this topic
- Some emails may be in folders not searched

### Recommendations
[Optional: suggest follow-up actions like checking meeting transcripts]
```

## Example Session

**User:** "I was told there were no open issues with Project Alpha during August. Can you verify?"

**Claude's process:**

1. **Clarify:**
   ```
   I'll search for evidence regarding:
   - Claim: "No open issues with Project Alpha during August"
   - Time period: August 1-31, 2025
   - Looking for: Any emails mentioning issues, problems, concerns, or blockers
   
   Should I proceed with this search?
   ```

2. **Search:**
   ```bash
   outlook-cli find --subject "Project Alpha" --after 2025-08-01 --before 2025-09-01 --toon --fields id,subject,from.emailAddress.address,receivedDateTime
   
   outlook-cli find --body "issue" --subject "Alpha" --after 2025-08-01 --before 2025-09-01 --toon
   # ... additional searches
   ```

3. **Read flagged emails:**
   ```bash
   outlook-cli read <flagged-id-1> --toon
   outlook-cli read <flagged-id-2> --toon
   ```

4. **Report:**
   ```
   ## Evidence Review: "No open issues with Project Alpha during August"
   
   ### Summary
   I found 2 emails that CONTRADICT this claim, documenting specific issues
   during the reviewed period.
   
   ### Key Evidence
   1. **Aug 12, 2025** - dev@company.com
      "We've hit a blocking issue with the database migration"
      Message ID: XYZ789
   
   2. **Aug 18, 2025** - qa@company.com  
      "Regression found in the new feature - marked as P1"
      Message ID: ABC456
   
   ### Conclusion
   The claim that there were "no open issues" during August appears to be
   inaccurate based on email evidence. Two documented issues were found.
   
   Would you like me to read these emails in full detail?
   ```

## Tips and Limitations

**Tips:**
- Use date ranges to bound the search scope
- Search both subject and body for comprehensive coverage
- Issue keywords to try: issue, problem, concern, blocker, delay, risk, bug, regression, failure
- Check meeting transcripts for verbal discussions

**Limitations:**
- Email only captures written communications
- Some discussions may be in chat or verbal
- Deleted emails are not searchable
- Shared folders may require different permissions
- Single email rarely proves a claim definitively—look for patterns

**Ethical considerations:**
- Present evidence objectively
- Include both supporting and contradicting evidence
- Note confidence levels and caveats
- Don't overstate conclusions

## Related Workflows

- [search-emails.md](search-emails.md) - Advanced search techniques
- [transcript-workflow.md](transcript-workflow.md) - Check meeting discussions
- [priority-review.md](priority-review.md) - Context-aware review
