# Org Structure Exploration Workflow

This workflow guides you through navigating NVIDIA's organizational hierarchy using `helios-cli`.

## Workflow Overview

1. Understand the exploration goal
2. Find the starting point (user or executive)
3. Navigate the org structure
4. Visualize relationships
5. Export or summarize findings

## Step-by-Step Process

### Step 1: Understand the Goal

**Common org exploration scenarios:**

| Goal | Approach |
|------|----------|
| "Who does X report to?" | Get manager chain |
| "Who reports to X?" | Find direct reports |
| "What's X's org look like?" | Build org tree |
| "How are X and Y connected?" | Find common manager |
| "Who's the VP for team X?" | Traverse up manager chain |
| "Who are the directors in org X?" | Search and filter by title |

**Clarify with user:**
```
To help explore the org structure, I need to understand:

1. Who are you starting from? (name, email, or executive)
2. What direction? (up to executives, down to reports, or sideways to peers)
3. How deep? (direct reports only, or full org tree)

Please describe what you're looking for.
```

### Step 2: Find the Starting Point

**Starting from a known user:**
```bash
helios-cli user get jdoe@nvidia.com | jq '{
  name: .attributes.cn,
  title: .attributes.title,
  department: .attributes.department,
  isManager: .attributes.isManager,
  directReports: .attributes.childUserCount,
  managerChainDepth: (.attributes.managerChain | length)
}'
```

**Starting from a department:**
```bash
# Find managers in a department
helios-cli user search --filter-department "Platform Engineering" --page-size 200 | jq '
  [.[] | select(.attributes.isManager == true)] |
  sort_by(.attributes.childUserCount) | reverse |
  .[] | {name: .attributes.cn, title: .attributes.title, directReports: .attributes.childUserCount}'
```

**Starting from an executive (finding who reports to them):**
```bash
# Find executive's directs group
helios-cli group get "Jensen Huang Directs"
```

### Step 3: Navigate Upward (Manager Chain)

**Get full manager chain:**
```bash
helios-cli user get jdoe@nvidia.com | jq -r '
  .attributes.managerChain | to_entries | .[] |
  "\(.key + 1). \(.value.name) (\(.value.login))"'
```

**Present as visual hierarchy:**
```
Manager Chain for John Doe
══════════════════════════════════════════

    ┌─────────────────────────────┐
    │  Jensen Huang (CEO)         │ Level 5
    └─────────────┬───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │  Michael Chen (SVP)         │ Level 4
    └─────────────┬───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │  Sarah Williams (VP)        │ Level 3
    └─────────────┬───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │  Robert Johnson (Director)  │ Level 2
    └─────────────┬───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │  Mary Smith (Manager)       │ Level 1 - Direct Manager
    └─────────────┬───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │  John Doe (You)             │
    └─────────────────────────────┘
```

**Find specific level (e.g., VP):**
```bash
helios-cli user get jdoe@nvidia.com | jq -r '
  .attributes.managerChain[] | select(.name | test("VP|Vice President"; "i")) |
  "\(.name) (\(.login))"'
```

### Step 4: Navigate Downward (Direct Reports)

**Get direct reports of a manager:**
```bash
MANAGER_LOGIN="msmith"
helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER_LOGIN" '
  [.[] | select(.attributes.manager == $mgr)] |
  sort_by(.attributes.cn) |
  .[] | {
    name: .attributes.cn,
    email: .attributes.email,
    title: .attributes.title,
    isManager: .attributes.isManager,
    directReports: .attributes.childUserCount
  }'
```

**Present as team list:**
```
Direct Reports of Mary Smith (8 people)
══════════════════════════════════════════

Team Members:
├── Alice Wang (Staff Engineer) - 0 reports
├── Bob Miller (Engineer) - 0 reports
├── Carol Davis (Senior Engineer) - 0 reports
├── David Lee (Tech Lead) ★ - 3 reports
├── Eve Johnson (Engineer) - 0 reports
├── Frank Chen (Senior Engineer) - 0 reports
├── Grace Kim (Engineer) - 0 reports
└── John Doe (Senior Engineer) - 0 reports

★ = Also a manager

Total: 8 direct reports
Sub-managers: 1 (David Lee with 3 reports)
Full org size: 11 people
```

**Build recursive org tree (2 levels):**
```bash
#!/bin/bash
# Get manager's directs
MANAGER="msmith"
echo "Org tree for $MANAGER:"
echo "════════════════════════════════════"

helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" -r '
  [.[] | select(.attributes.manager == $mgr)] |
  sort_by(.attributes.cn) |
  .[] | "├── \(.attributes.cn) (\(.attributes.login)) - \(.attributes.title)"'

# For each sub-manager, get their reports
helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" -r '
  [.[] | select(.attributes.manager == $mgr and .attributes.isManager == true)] |
  .[].attributes.login' | while read -r submgr; do
    echo "    └── Reports of $submgr:"
    helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$submgr" -r '
      [.[] | select(.attributes.manager == $mgr)] |
      .[] | "        ├── \(.attributes.cn)"'
done
```

### Step 5: Navigate Sideways (Peers)

**Find peers (same manager):**
```bash
# First get the user's manager
MANAGER=$(helios-cli user get jdoe@nvidia.com | jq -r '.attributes.manager')

# Then find all people with same manager
helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" '
  [.[] | select(.attributes.manager == $mgr)] |
  sort_by(.attributes.cn) |
  .[] | {name: .attributes.cn, title: .attributes.title, email: .attributes.email}'
```

**Find people at same level in different teams:**
```bash
# Get user's title level
TITLE=$(helios-cli user get jdoe@nvidia.com | jq -r '.attributes.title')

# Find others with similar title
helios-cli user search --filter-department "Engineering" --page-size 500 | jq --arg title "$TITLE" '
  [.[] | select(.attributes.title == $title)] |
  .[] | {name: .attributes.cn, department: .attributes.department}'
```

### Step 6: Find Connections Between Two People

**Check if two people share a manager chain:**
```bash
USER1="jdoe@nvidia.com"
USER2="asmith@nvidia.com"

# Get both manager chains
CHAIN1=$(helios-cli user get "$USER1" | jq '[.attributes.managerChain[].login]')
CHAIN2=$(helios-cli user get "$USER2" | jq '[.attributes.managerChain[].login]')

# Find common managers
echo "$CHAIN1" "$CHAIN2" | jq -s '
  (.[0] | map({login: ., source: "user1"})) +
  (.[1] | map({login: ., source: "user2"})) |
  group_by(.login) |
  map(select(length > 1)) |
  .[0][0].login' -r
```

**Present connection:**
```
Connection between John Doe and Alice Smith
══════════════════════════════════════════

John Doe                    Alice Smith
    │                           │
    ├── Mary Smith              ├── Bob Johnson
    │   (Manager)               │   (Manager)
    │                           │
    └───────────┬───────────────┘
                │
        Robert Johnson
        (Common Manager - Director)
                │
        Sarah Williams
        (VP Engineering)
                │
           [...]

Organizational Distance: 3 levels apart
Common Manager: Robert Johnson (Director)
```

## Advanced Exploration Scenarios

### Find All Directors in an Org

```bash
helios-cli user search --filter-active true --page-size 1000 | jq '
  [.[] | select(.attributes.title | test("Director"; "i"))] |
  sort_by(.attributes.department) |
  .[] | {name: .attributes.cn, title: .attributes.title, department: .attributes.department, reports: .attributes.childUserCount}'
```

### Find Largest Teams

```bash
helios-cli user search --filter-active true --page-size 1000 | jq '
  [.[] | select(.attributes.isManager == true)] |
  sort_by(.attributes.childUserCount) | reverse |
  .[:10] |
  .[] | {name: .attributes.cn, title: .attributes.title, directReports: .attributes.childUserCount}'
```

### Map Full Org Under an Executive

```bash
#!/bin/bash
# This creates a full org export under a specific executive

EXEC="swilliams"  # VP to start from

echo "Full org under $EXEC"
echo "========================"

# Recursive function to print org
print_org() {
  local manager=$1
  local indent=$2
  
  helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$manager" -r '
    [.[] | select(.attributes.manager == $mgr)] |
    sort_by(.attributes.cn) |
    .[] | "\(.attributes.login)|\(.attributes.cn)|\(.attributes.isManager)"' | while IFS='|' read -r login name is_mgr; do
      echo "${indent}├── $name"
      if [ "$is_mgr" = "true" ]; then
        print_org "$login" "${indent}│   "
      fi
  done
}

print_org "$EXEC" ""
```

### Calculate Org Metrics

```bash
helios-cli user search --filter-active true --page-size 1000 | jq '
  {
    total_employees: length,
    managers: [.[] | select(.attributes.isManager == true)] | length,
    individual_contributors: [.[] | select(.attributes.isManager == false)] | length,
    avg_span_of_control: ([.[] | select(.attributes.isManager == true) | .attributes.childUserCount] | add / length),
    by_country: (group_by(.attributes.country) | map({country: .[0].attributes.country, count: length}) | sort_by(.count) | reverse),
    by_department: (group_by(.attributes.department) | map({dept: .[0].attributes.department, count: length}) | sort_by(.count) | reverse | .[:10])
  }'
```

## Error Handling

### User Not Found in Chain
```
The specified user was not found. They may have:
- Left the organization
- Changed roles recently
- Have a different identifier than expected

Try searching by name: helios-cli user search --filter-active true | jq '.[] | select(.attributes.cn | test("Name"; "i"))'
```

### Circular References (Rare)
```
Warning: Detected potential circular reference in org structure.
This is a data issue that should be reported to HR/IT.

Continuing with available data...
```

### Incomplete Data
```
Note: Some users in this org may have incomplete profile data.
Missing manager information for 3 users.
Showing available data:
[...]
```

## Example Conversations

### Simple Chain Query

**User:** Who does Jennifer Wong report to?

**Assistant:** Let me look up Jennifer Wong's reporting chain...

```
Manager Chain for Jennifer Wong
══════════════════════════════════════════

Jennifer Wong (Engineer)
    │
    └── Reports to: David Chen (Manager)
        └── Reports to: Lisa Park (Director)
            └── Reports to: Michael Brown (VP)
                └── Reports to: Jensen Huang (CEO)

Direct manager: David Chen (dchen@nvidia.com)
```

### Team Exploration

**User:** Show me the full org under Sarah Williams

**Assistant:** Let me map out Sarah Williams' organization...

```
Organization under Sarah Williams (VP Engineering)
══════════════════════════════════════════

Sarah Williams (VP Engineering) - 4 direct reports, 156 total
├── Robert Johnson (Director) - 45 people
│   ├── Mary Smith (Manager) - 8 people
│   │   ├── John Doe (Senior Engineer)
│   │   ├── Alice Wang (Staff Engineer)
│   │   └── [6 more...]
│   ├── Tom Wilson (Manager) - 12 people
│   └── [3 more managers...]
├── Lisa Chen (Director) - 38 people
│   └── [...]
├── James Lee (Director) - 52 people
│   └── [...]
└── Karen Davis (Sr Manager) - 21 people
    └── [...]

Summary:
- Total in org: 156 people
- Directors: 3
- Managers: 12
- Individual Contributors: 141
```

### Connection Finding

**User:** How are John Doe and Alice Chen connected organizationally?

**Assistant:** Let me trace the organizational connection...

```
Organizational Connection
══════════════════════════════════════════

John Doe (Platform Engineering)
    │
    ├── Mary Smith (Manager)
    │
    └── Robert Johnson (Director) ◄── Common Manager
        │
        ├── Lisa Park (Manager)
        │
        └── Alice Chen (Cloud Services)

Connection: Both report up to Robert Johnson (Director)
Distance: 2 levels (through their respective managers)
Same VP: Yes (Sarah Williams)
```

## Tips for Org Exploration

1. **Start from known point**: Always start with someone you know
2. **Go up before down**: Manager chains are faster than recursive descent
3. **Use filters**: Filter by department/site to reduce data
4. **Cache large results**: Store search results for multiple queries
5. **Verify with multiple sources**: Org data may lag behind reality
6. **Consider timing**: Recent reorgs may not be reflected yet
