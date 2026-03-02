# Priority-Based Review Workflow

This prompt guides you through helping users review emails based on their stated priorities or context from external documents.

## Use Case

User provides priorities from:
- An attached document or file
- A verbal description of current projects
- A list of topics they care about

Goal: Find and flag/highlight the most relevant emails based on those priorities.

## Workflow Overview

1. Extract priorities from user context
2. Build search queries from priorities
3. Search and retrieve candidate emails
4. Reason about relevance to rank results
5. Present top N and offer actions

## Step-by-Step Process

### Step 1: Extract Priorities from Context

**If user provides a file/document:**
1. Read the document content
2. Extract key themes, project names, people, deadlines
3. Identify keywords suitable for email search

**If user describes priorities verbally:**
1. Identify the main topics/projects mentioned
2. Note any specific people, deadlines, or keywords
3. Ask clarifying questions if priorities are vague

**Present extracted priorities to user:**
```
I've identified these priorities from your input:
1. [Project/Topic A] - keywords: "budget", "Q4", "planning"
2. [Project/Topic B] - keywords: "migration", "AWS", "deadline"
3. [Person C] - emails from/to this person
4. [Deadline D] - time-sensitive items

Does this capture your priorities correctly?
```

### Step 2: Build Search Queries

Map priorities to outlook-cli search filters:

**For topics/keywords:**
```bash
# Subject contains keyword
outlook-cli find --subject "budget" --toon --fields id,subject,from.emailAddress.address,receivedDateTime

# Body contains keyword
outlook-cli find --body "migration" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

**For specific people:**
```bash
# From a specific person
outlook-cli find --from "boss@company.com" --toon --fields id,subject,receivedDateTime

# Involving a specific person (any field)
outlook-cli find --participants "colleague@company.com" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

**For time-sensitive items:**
```bash
# Recent emails only
outlook-cli find --after 2025-01-25 --toon --fields id,subject,from.emailAddress.address,receivedDateTime

# Combined with keywords
outlook-cli find --subject "deadline" --after 2025-01-20 --toon --fields id,subject,from.emailAddress.address,receivedDateTime
```

**Strategy for multiple priorities:**
- Run separate searches for each priority
- Combine results and look for emails matching multiple criteria
- Weight emails that match multiple priorities higher

### Step 3: Search and Collect Candidates

Execute searches for each priority:

```bash
# Priority 1: Budget-related
outlook-cli find --subject "budget" --only-unread --toon --fields id,subject,from.emailAddress.address,receivedDateTime

# Priority 2: Migration project
outlook-cli find --body "migration" --only-unread --toon --fields id,subject,from.emailAddress.address,receivedDateTime

# Priority 3: From key stakeholder
outlook-cli find --from "stakeholder@company.com" --only-unread --toon --fields id,subject,receivedDateTime
```

**For each search, note:**
- Email IDs that appear
- Which priority they match
- Whether they appear in multiple searches (higher relevance)

### Step 4: Reason About Relevance

For promising candidates, read full content to assess relevance:

```bash
outlook-cli read <message-id> --toon
```

**Relevance scoring criteria:**
1. **Direct match**: Subject explicitly mentions priority topic (+3)
2. **Indirect match**: Body contains related keywords (+2)
3. **Sender relevance**: From key person related to priority (+2)
4. **Time sensitivity**: Contains deadline/urgency language (+1)
5. **Multiple priorities**: Matches 2+ priorities (+2 each)

**Build a ranked list:**
```
Based on your priorities, here are the most relevant emails:

1. ⭐⭐⭐ (Score: 7) "Q4 Budget Final Review"
   - From: cfo@company.com
   - Matches: Budget priority, key stakeholder, urgent language
   - Preview: "Need your approval by EOD..."

2. ⭐⭐⭐ (Score: 6) "Migration Timeline Update"
   - From: techops@company.com
   - Matches: Migration project, deadline mentioned
   - Preview: "Revised timeline attached..."

3. ⭐⭐ (Score: 4) "Weekly Status Report"
   - From: pm@company.com
   - Matches: Mentions budget and migration
   - Preview: "Updates on both projects..."

[... continue for top N ...]
```

### Step 5: Present and Offer Actions

**Present top N emails with relevance explanation:**

Show why each email is relevant to their stated priorities. This helps users understand the ranking.

**Offer actions:**
```
What would you like to do with these emails?

1. Flag all [N] as follow-up
2. Flag only the top 3
3. Read any of these in detail (provide number)
4. Archive emails NOT in this list
5. Search with different criteria
```

**Execute requested actions:**

```bash
# Flag as follow-up
outlook-cli mark <message-id> --flag --toon

# Flag multiple
for id in msg1 msg2 msg3; do
  outlook-cli mark $id --flag --toon
done
```

## Example Session

**User:** "Flag the top 5 emails relevant to priorities in this document: [Product Roadmap Q1 2026.pdf]"

**Claude's process:**

1. **Read document**, extract: "Mobile app launch", "API v2 migration", "Customer feedback integration", key people: Sarah (PM), Alex (Eng Lead)

2. **Confirm with user:**
   ```
   I've identified these priorities from the roadmap:
   - Mobile app launch (keywords: "mobile", "app", "launch", "iOS", "Android")
   - API v2 migration (keywords: "API", "v2", "migration", "endpoint")
   - Customer feedback (keywords: "feedback", "NPS", "user research")
   - Key stakeholders: Sarah, Alex
   
   Should I search for emails matching these priorities?
   ```

3. **Execute searches:**
   ```bash
   outlook-cli find --subject "mobile app" --only-unread --toon --fields id,subject,from.emailAddress.address
   outlook-cli find --body "API v2" --only-unread --toon --fields id,subject,from.emailAddress.address
   outlook-cli find --from "sarah@company.com" --only-unread --toon --fields id,subject,receivedDateTime
   # ... etc
   ```

4. **Read promising candidates, score relevance**

5. **Present ranked results:**
   ```
   Top 5 emails most relevant to your Q1 priorities:
   
   1. "Mobile App Beta Feedback" (Score: 8)
      Matches: Mobile app launch + Customer feedback
      
   2. "API v2 Migration Blockers" from Alex (Score: 7)
      Matches: API migration + Key stakeholder
      
   [... etc ...]
   
   Would you like me to flag these 5 emails for follow-up?
   ```

6. **On confirmation:**
   ```bash
   outlook-cli mark msg1 --flag --toon
   outlook-cli mark msg2 --flag --toon
   # ... etc
   ```

## Tips and Limitations

**Tips:**
- Start with unread emails (`--only-unread`) to focus on actionable items
- Use `--fields` to minimize token usage during search phase
- Read full emails only for top candidates to manage token budget
- Ask user to clarify if priorities are ambiguous

**Limitations:**
- Search is keyword-based; semantic similarity requires Claude's reasoning
- Graph API doesn't support fuzzy matching—exact or substring matches only
- For very broad priorities, narrow with date ranges (`--after`)
- Large result sets may need sampling strategies (see email-triage.md)

## Related Workflows

- [email-triage.md](email-triage.md) - General inbox triage
- [search-emails.md](search-emails.md) - Advanced search techniques
- [open-loops-review.md](open-loops-review.md) - Review pending items
