# Team Analysis Workflow

This workflow guides you through analyzing teams, departments, and organizational units using `helios-cli`.

## Workflow Overview

1. Define the analysis scope
2. Gather team data
3. Compute metrics and statistics
4. Generate insights and visualizations
5. Export results

## Step-by-Step Process

### Step 1: Define Analysis Scope

**Clarify what to analyze:**

| Scope | Description | Approach |
|-------|-------------|----------|
| Single Manager's Team | Direct reports only | Filter by manager |
| Full Org | All reports recursively | Traverse org tree |
| Department | Everyone in a department | Filter by department |
| Site/Location | Everyone at a location | Filter by site |
| Cross-functional | Multiple criteria | Combine filters |

**Questions to clarify:**
```
To analyze the team, I need to understand:

1. What's the scope?
   - Direct reports of one manager
   - Full organization under someone
   - A specific department
   - A geographic location

2. What metrics are you interested in?
   - Headcount and composition
   - Tenure distribution
   - Title/level distribution
   - Geographic spread
   - Employment types

3. What output do you need?
   - Summary statistics
   - Detailed roster
   - Exportable data (CSV)
   - Charts/visualizations

Please describe what you're analyzing.
```

### Step 2: Gather Team Data

**For a manager's direct reports:**
```bash
MANAGER="msmith"
helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" '
  [.[] | select(.attributes.manager == $mgr)]'
```

**For a full organization (recursive):**
```bash
#!/bin/bash
# gather_org.sh - Recursively gather all reports under a manager

gather_org() {
  local manager=$1
  
  # Get direct reports
  helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$manager" '
    [.[] | select(.attributes.manager == $mgr)]' > "/tmp/org_$manager.json"
  
  # Get sub-managers and recurse
  jq -r '.[] | select(.attributes.isManager == true) | .attributes.login' "/tmp/org_$manager.json" | while read -r submgr; do
    gather_org "$submgr"
  done
}

# Start from top
gather_org "swilliams"

# Combine all results
cat /tmp/org_*.json | jq -s 'add | unique_by(.attributes.login)'
```

**For a department:**
```bash
helios-cli user search --filter-department "Platform Engineering" --filter-active true --page-size 500
```

**For a site:**
```bash
helios-cli user search --filter-site "Santa Clara" --filter-active true --page-size 500
```

### Step 3: Compute Metrics

**Headcount Summary:**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
{
  total_headcount: length,
  by_employment_type: (group_by(.attributes.employmentType) | map({type: .[0].attributes.employmentType, count: length})),
  managers: [.[] | select(.attributes.isManager == true)] | length,
  individual_contributors: [.[] | select(.attributes.isManager == false)] | length,
  manager_ratio: (([.[] | select(.attributes.isManager == true)] | length) / length * 100 | round / 100)
}'
```

**Tenure Distribution:**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  def years_since(date): ((now - (date | fromdateiso8601)) / 31536000 | floor);
  def tenure_bucket(years):
    if years < 1 then "< 1 year"
    elif years < 2 then "1-2 years"
    elif years < 5 then "2-5 years"
    elif years < 10 then "5-10 years"
    else "10+ years"
    end;
  
  group_by(.attributes.hireDate | tenure_bucket(years_since(.))) |
  map({tenure: .[0].attributes.hireDate | tenure_bucket(years_since(.)), count: length}) |
  sort_by(.tenure)'
```

**Simplified Tenure (by year):**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  [.[] | .attributes.hireDate | split("-")[0]] |
  group_by(.) |
  map({year: .[0], count: length}) |
  sort_by(.year) | reverse'
```

**Geographic Distribution:**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
{
  by_country: (group_by(.attributes.country) | map({country: .[0].attributes.country, count: length}) | sort_by(.count) | reverse),
  by_site: (group_by(.attributes.site) | map({site: .[0].attributes.site, count: length}) | sort_by(.count) | reverse | .[:10])
}'
```

**Title Distribution:**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  group_by(.attributes.title) |
  map({title: .[0].attributes.title, count: length}) |
  sort_by(.count) | reverse | .[:15]'
```

**Manager Span of Control:**
```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  [.[] | select(.attributes.isManager == true)] |
  {
    total_managers: length,
    avg_span: ([.[].attributes.childUserCount] | add / length | . * 10 | round / 10),
    min_span: ([.[].attributes.childUserCount] | min),
    max_span: ([.[].attributes.childUserCount] | max),
    span_distribution: (group_by(.attributes.childUserCount | . / 3 | floor * 3) | 
      map({range: "\(.[0].attributes.childUserCount | . / 3 | floor * 3)-\(.[0].attributes.childUserCount | . / 3 | floor * 3 + 2)", count: length}))
  }'
```

### Step 4: Generate Insights

**Present comprehensive analysis:**

```
Team Analysis: Platform Engineering
══════════════════════════════════════════

📊 HEADCOUNT SUMMARY
├── Total: 156 employees
├── Full-Time (FTE): 142 (91%)
├── Contractors: 12 (8%)
└── Interns: 2 (1%)

👥 ORGANIZATION STRUCTURE
├── Managers: 14 (9%)
├── Individual Contributors: 142 (91%)
├── Avg Span of Control: 10.1 direct reports
└── Largest Team: David Chen (18 reports)

📅 TENURE DISTRIBUTION
├── < 1 year:   ████████░░░░ 28 (18%)
├── 1-2 years:  ██████░░░░░░ 22 (14%)
├── 2-5 years:  ████████████ 48 (31%)
├── 5-10 years: ██████████░░ 38 (24%)
└── 10+ years:  █████░░░░░░░ 20 (13%)

🌍 GEOGRAPHIC DISTRIBUTION
├── United States: 98 (63%)
│   ├── Santa Clara: 52
│   ├── Seattle: 28
│   └── Austin: 18
├── India: 35 (22%)
│   ├── Bangalore: 25
│   └── Pune: 10
├── Taiwan: 12 (8%)
└── Other: 11 (7%)

📈 GROWTH TREND (Hires by Year)
├── 2025: 18 new hires
├── 2024: 32 new hires
├── 2023: 28 new hires
├── 2022: 25 new hires
└── Earlier: 53 employees

💼 TOP TITLES
├── Software Engineer: 45
├── Senior Software Engineer: 38
├── Staff Engineer: 22
├── Principal Engineer: 12
└── Engineering Manager: 14
```

### Step 5: Export Results

**Export to CSV:**
```bash
helios-cli user search --filter-department "Platform Engineering" --filter-active true --page-size 500 | jq -r '
  ["Name", "Email", "Title", "Manager", "Site", "Country", "Hire Date", "Type"],
  (.[] | [
    .attributes.cn,
    .attributes.email,
    .attributes.title,
    .attributes.manager,
    .attributes.site,
    .attributes.country,
    .attributes.hireDate,
    .attributes.employmentType
  ]) | @csv' > team_roster.csv

echo "Exported to team_roster.csv"
```

**Export to JSON (for further processing):**
```bash
helios-cli user search --filter-department "Platform Engineering" --filter-active true --page-size 500 | jq '
  [.[] | {
    name: .attributes.cn,
    email: .attributes.email,
    title: .attributes.title,
    department: .attributes.department,
    manager: .attributes.manager,
    site: .attributes.site,
    country: .attributes.country,
    hireDate: .attributes.hireDate,
    isManager: .attributes.isManager,
    directReports: .attributes.childUserCount
  }]' > team_data.json
```

**Export summary statistics:**
```bash
helios-cli user search --filter-department "Platform Engineering" --filter-active true --page-size 500 | jq '
{
  generated: (now | todate),
  department: "Platform Engineering",
  metrics: {
    headcount: length,
    managers: [.[] | select(.attributes.isManager == true)] | length,
    ics: [.[] | select(.attributes.isManager == false)] | length,
    by_country: (group_by(.attributes.country) | map({country: .[0].attributes.country, count: length})),
    by_type: (group_by(.attributes.employmentType) | map({type: .[0].attributes.employmentType, count: length}))
  }
}' > team_summary.json
```

## Advanced Analysis Scenarios

### Compare Two Teams

```bash
#!/bin/bash
# compare_teams.sh

TEAM1="Platform Engineering"
TEAM2="Cloud Services"

echo "Comparing $TEAM1 vs $TEAM2"
echo "═══════════════════════════════════════"

# Get both teams
T1=$(helios-cli user search --filter-department "$TEAM1" --filter-active true --page-size 500)
T2=$(helios-cli user search --filter-department "$TEAM2" --filter-active true --page-size 500)

echo "$T1" | jq --arg name "$TEAM1" '{
  team: $name,
  headcount: length,
  managers: [.[] | select(.attributes.isManager == true)] | length,
  avg_tenure_years: ([.[].attributes.hireDate | split("-")[0] | tonumber] | add / length | 2025 - . | . * 10 | round / 10)
}'

echo "$T2" | jq --arg name "$TEAM2" '{
  team: $name,
  headcount: length,
  managers: [.[] | select(.attributes.isManager == true)] | length,
  avg_tenure_years: ([.[].attributes.hireDate | split("-")[0] | tonumber] | add / length | 2025 - . | . * 10 | round / 10)
}'
```

### Identify Flight Risks (Long Tenure, No Promotion)

```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  [.[] | select(
    (.attributes.hireDate < "2020-01-01") and
    (.attributes.title | test("Senior|Staff|Principal|Director|VP"; "i") | not)
  )] |
  sort_by(.attributes.hireDate) |
  .[] | {
    name: .attributes.cn,
    title: .attributes.title,
    hireDate: .attributes.hireDate,
    manager: .attributes.manager
  }'
```

### New Hire Onboarding Status

```bash
# Find all hires in last 90 days
helios-cli user search --filter-active true --page-size 500 | jq --arg date "2025-01-01" '
  [.[] | select(.attributes.hireDate > $date)] |
  sort_by(.attributes.hireDate) | reverse |
  .[] | {
    name: .attributes.cn,
    email: .attributes.email,
    hireDate: .attributes.hireDate,
    manager: .attributes.manager,
    department: .attributes.department,
    site: .attributes.site
  }'
```

### Diversity Analysis by Level

```bash
helios-cli user search --filter-department "Engineering" --filter-active true --page-size 1000 | jq '
  def level(title):
    if title | test("VP|Vice President") then "VP"
    elif title | test("Director") then "Director"
    elif title | test("Principal|Distinguished") then "Principal"
    elif title | test("Staff") then "Staff"
    elif title | test("Senior") then "Senior"
    elif title | test("Manager") then "Manager"
    else "IC"
    end;
  
  group_by(.attributes.title | level(.)) |
  map({
    level: .[0].attributes.title | level(.),
    count: length,
    by_country: (group_by(.attributes.country) | map({country: .[0].attributes.country, count: length}) | sort_by(.count) | reverse)
  }) |
  sort_by(.level)'
```

## Error Handling

### Large Dataset Warnings
```
⚠️ Warning: Query returned 2,847 results.

This may:
- Take a long time to process
- Use significant memory
- Hit API rate limits

Recommendations:
- Add filters to narrow scope
- Use pagination with --page-size
- Export incrementally by site or department

Continue anyway? [Y/N]
```

### Incomplete Data
```
Note: Analysis may be incomplete.
- 12 users missing hire dates
- 5 users missing department info
- 3 users missing site info

Excluding incomplete records from relevant calculations.
Continuing with 98% complete data...
```

### Rate Limiting Recovery
```bash
# Rate-limit safe team analysis
analyze_with_retry() {
  local dept=$1
  local retries=3
  
  for i in $(seq 1 $retries); do
    result=$(helios-cli user search --filter-department "$dept" --filter-active true --page-size 200 2>&1)
    if echo "$result" | grep -q "rate_limit"; then
      echo "Rate limited, waiting 60s (attempt $i/$retries)..."
      sleep 60
    else
      echo "$result"
      return 0
    fi
  done
  echo "Failed after $retries attempts"
  return 1
}
```

## Example Conversations

### Quick Team Summary

**User:** How big is the Platform Engineering team?

**Assistant:** Let me analyze Platform Engineering...

```
Platform Engineering Team Summary
══════════════════════════════════════════

📊 Headcount: 156 employees

By Type:
├── FTE: 142 (91%)
├── Contractors: 12 (8%)
└── Interns: 2 (1%)

By Level:
├── Managers: 14
├── Staff+: 22
├── Senior: 45
└── Junior/Mid: 75

Top Locations:
├── Santa Clara: 52
├── Bangalore: 28
└── Seattle: 22

Recent Growth: +18 hires in 2025

Would you like detailed breakdown by sub-team or tenure analysis?
```

### Detailed Comparison

**User:** Compare Platform Engineering vs Cloud Services teams

**Assistant:** Let me pull data for both teams...

```
Team Comparison
══════════════════════════════════════════

                    Platform Eng    Cloud Services
─────────────────────────────────────────────────
Headcount              156             89
Managers               14              8
Avg Team Size          11.1            11.1
Avg Tenure (yrs)       3.8             2.9

By Employment Type:
FTE                    91%             88%
Contractor             8%              10%
Intern                 1%              2%

By Geography:
US                     63%             72%
India                  22%             18%
Other                  15%             10%

2025 Hires             18              12
Growth Rate            +12%            +16%

Key Insights:
- Cloud Services is growing faster (+16% vs +12%)
- Platform Engineering has more tenured staff
- Similar manager ratios (~9%)
- Cloud Services more US-concentrated
```

## Tips for Effective Team Analysis

1. **Start with scope**: Define exactly what "team" means (direct reports vs full org)
2. **Use filters early**: Reduce data volume before complex analysis
3. **Validate counts**: Cross-check with known team sizes
4. **Consider timing**: Data may lag organizational changes
5. **Export for tracking**: Save snapshots for historical comparison
6. **Watch for edge cases**: Contractors, interns, and temps may have different attributes
7. **Combine sources**: Helios data + other sources for complete picture
