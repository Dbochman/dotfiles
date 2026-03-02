# Category Prioritization Workflow

This prompt guides you through color-coding calendar events by priority using Outlook categories.

## Use Case

User wants visual organization of their calendar:
- "Color code my meetings by importance"
- "Categorize my events based on my Q1 priorities"
- "Tag meetings related to Project X"

## Current Capabilities

| Feature | Support | How |
|---------|---------|-----|
| List categories | ✅ | `calendar-cli categories` |
| List events | ✅ | `calendar-cli find` |
| Apply categories | ✅ | `calendar-cli update --category` |
| Priority reasoning | ✅ | Claude analyzes based on context |

## Workflow Overview

1. Understand user priorities
2. List available categories
3. Find events to categorize
4. Assign priorities
5. Apply categories with confirmation

## Step-by-Step Process

### Step 1: Understand User Priorities

**Get context from user:**
```
To categorize your meetings by importance, I need to understand:

1. What are your current priorities? (projects, goals, themes)
2. What time period? (this week, this month)
3. How do you want to categorize?
   - By priority (High/Medium/Low)
   - By project/theme
   - By action required (Attend/Optional/Decline)
```

**Example priority mapping:**
```
Based on your Q1 priorities:
- 🔴 Red = Critical (customer meetings, deadlines, exec reviews)
- 🟠 Orange = High (project milestones, 1:1s with manager)
- 🟡 Yellow = Medium (team meetings, routine syncs)
- 🟢 Green = Low/Optional (newsletters, social, FYI meetings)
```

### Step 2: List Available Categories

```bash
calendar-cli categories --toon
```

**Show available categories:**
```
Available categories in your Outlook:
  🔴 Red Category
  🟠 Orange Category
  🟡 Yellow Category
  🟢 Green Category
  🔵 Blue Category
  🟣 Purple Category

Which mapping would you like to use?
1. Priority (Red=Critical, Orange=High, Yellow=Medium, Green=Low)
2. Custom mapping (tell me your preferences)
```

### Step 3: Find Events to Categorize

```bash
# This week's events
calendar-cli find --after 2025-02-01 --before 2025-02-08 --toon --fields id,subject,organizer.emailAddress.name,start.dateTime
```

**Present events for categorization:**
```
Found 15 events this week:

1. Mon 9am - Customer Demo with Acme Corp
   Organizer: sales@company.com
   
2. Mon 11am - Team Standup
   Organizer: pm@company.com

3. Tue 2pm - 1:1 with Manager
   Organizer: boss@company.com

4. Wed 10am - All-Hands (optional)
   Organizer: hr@company.com

...
```

### Step 4: Assign Priorities

**Apply priority reasoning:**
```
Based on your Q1 priorities (customer success, Project X delivery):

🔴 CRITICAL (Red Category):
1. Customer Demo with Acme Corp - customer-facing
2. Project X Review - key deliverable

🟠 HIGH (Orange Category):
3. 1:1 with Manager - important relationship
4. Sprint Planning - affects delivery

🟡 MEDIUM (Yellow Category):
5. Team Standup - routine but valuable
6. Design Review - supports Project X

🟢 LOW/OPTIONAL (Green Category):
7. All-Hands - optional attendance
8. Newsletter Review - can catch up async

Does this categorization look right? I can adjust before applying.
```

### Step 5: Apply Categories with Confirmation

**Get explicit approval:**
```
Ready to apply categories to 8 events:
- 2 events → Red Category (Critical)
- 2 events → Orange Category (High)
- 2 events → Yellow Category (Medium)
- 2 events → Green Category (Low)

Should I proceed? [Yes/No/Modify]
```

**On approval:**
```bash
# Apply categories
calendar-cli update <event-id-1> --category "Red Category" --toon
calendar-cli update <event-id-2> --category "Red Category" --toon
calendar-cli update <event-id-3> --category "Orange Category" --toon
# ... continue for all events
```

**Confirm completion:**
```
✅ Categorization complete!

Applied categories to 8 events:
- 🔴 Red: Customer Demo, Project X Review
- 🟠 Orange: 1:1 with Manager, Sprint Planning
- 🟡 Yellow: Team Standup, Design Review
- 🟢 Green: All-Hands, Newsletter Review

Your calendar now shows color-coded priorities in Outlook.
```

## Example Session

**User:** "Color code my meetings this week based on my OKRs"

**Claude's process:**

1. **Ask for OKRs:**
   ```
   What are your key OKRs this quarter? This will help me categorize:
   - 🔴 Red = Directly supports OKR
   - 🟡 Yellow = Indirectly supports OKR
   - 🟢 Green = Doesn't align with OKRs
   ```

2. **Get events:**
   ```bash
   calendar-cli find --after 2025-02-01 --before 2025-02-08 --toon
   ```

3. **Propose mapping:**
   ```
   Based on your OKRs (Ship v2.0, Improve NPS):
   
   🔴 RED (OKR-critical):
   - Sprint Planning (v2.0 delivery)
   - Customer Feedback Review (NPS)
   
   🟡 YELLOW (OKR-adjacent):
   - Team Standup (team health)
   - 1:1s (retention)
   
   🟢 GREEN (Not OKR-aligned):
   - All-Hands
   - Social events
   
   Apply these categories?
   ```

4. **Execute on confirmation:**
   ```bash
   calendar-cli update evt001 --category "Red Category" --toon
   calendar-cli update evt002 --category "Red Category" --toon
   ...
   ```

## Tips and Limitations

**Tips:**
- Start with a clear priority framework before categorizing
- Use consistent category meanings across weeks
- Review and adjust as priorities change
- Categories are visible in Outlook's calendar view

**Limitations:**
- Categories are per-event (cannot bulk-apply in one command)
- Changes only affect your view (not other attendees)
- Limited to Outlook's preset category colors
- Maximum ~25 preset colors available

**Category best practices:**
- Keep the mapping simple (3-4 priority levels)
- Document your category meanings
- Re-categorize weekly as priorities shift
- Use for visual triage, not strict rules

## Related Workflows

- [focus-time.md](focus-time.md) - Protect time for priorities
- [priority-review.md](priority-review.md) - Email prioritization
- [calendar-review.md](calendar-review.md) - Schedule analysis
