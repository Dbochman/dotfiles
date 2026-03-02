# Group Membership Workflow

This workflow guides you through checking, verifying, and analyzing security group and team memberships using `helios-cli`.

## Workflow Overview

1. Understand the membership query
2. Check membership status
3. Explore group details
4. Analyze membership paths
5. Report findings

## Step-by-Step Process

### Step 1: Understand the Query

**Common membership queries:**

| Query Type | Example | Approach |
|------------|---------|----------|
| Direct check | "Is John in group X?" | `check-membership` |
| List user's groups | "What groups is John in?" | `relationship user-groups` |
| List group members | "Who's in group X?" | Search + filter |
| Verify access | "Does John have admin access?" | Check relevant groups |
| Path finding | "How is John connected to group X?" | `relationships` |

**Clarify the request:**
```
To check membership, I need to understand:

1. Who are you checking?
   - A specific user (name/email)
   - Multiple users (list or team)

2. What group(s)?
   - Specific security group name
   - Access level (admin, read, etc.)
   - Org-based group (e.g., "VP Directs")

3. What's the purpose?
   - Access verification
   - Audit/compliance
   - Org understanding

Please provide the user(s) and group(s) to check.
```

### Step 2: Check Membership Status

**Direct membership check:**
```bash
helios-cli report check-membership jdoe@nvidia.com "Platform-Admin-Access"
```

**Response interpretation:**
```json
{
  "isMember": true,
  "membershipType": "direct",
  "group": "Platform-Admin-Access"
}
```

Or:
```json
{
  "isMember": true,
  "membershipType": "nested",
  "through": ["Platform-Engineers", "Engineering-All"],
  "group": "Platform-Admin-Access"
}
```

Or:
```json
{
  "isMember": false,
  "group": "Platform-Admin-Access"
}
```

**Present results:**
```
Membership Check: John Doe → Platform-Admin-Access
══════════════════════════════════════════

✅ MEMBER

Membership Type: Nested (through group hierarchy)
Path: John Doe
      └── Platform-Engineers (direct member)
          └── Engineering-All (parent group)
              └── Platform-Admin-Access (target)

This means John has admin access through his team membership.
```

Or:
```
Membership Check: John Doe → Platform-Admin-Access
══════════════════════════════════════════

❌ NOT A MEMBER

John Doe is not in Platform-Admin-Access.

To get access, John could:
1. Request direct addition to Platform-Admin-Access
2. Join a group that has nested membership:
   - Platform-Engineers
   - Platform-Leads
   - Engineering-Admins

Would you like me to check who can approve access?
```

### Step 3: List User's Groups

**Get all groups for a user:**
```bash
helios-cli relationship user-groups --page-size 100 | jq '
  [.[] | select(.attributes.userDn | test("John Doe"; "i"))] |
  .[].attributes.groupName' -r
```

**Categorize groups:**
```bash
helios-cli relationship user-groups --page-size 100 | jq '
  [.[] | select(.attributes.userDn | test("John Doe"; "i"))] |
  {
    security_groups: [.[] | select(.attributes.groupName | test("Access|Admin|Security"; "i")) | .attributes.groupName],
    org_groups: [.[] | select(.attributes.groupName | test("Directs|Team|Org"; "i")) | .attributes.groupName],
    distribution_lists: [.[] | select(.attributes.groupName | test("DL-|List-"; "i")) | .attributes.groupName],
    other: [.[] | select(.attributes.groupName | test("Access|Admin|Security|Directs|Team|Org|DL-|List-"; "i") | not) | .attributes.groupName]
  }'
```

**Present categorized groups:**
```
Groups for John Doe
══════════════════════════════════════════

🔐 SECURITY/ACCESS GROUPS (5)
├── Platform-Admin-Access
├── AWS-ReadOnly
├── GitHub-Platform-Team
├── Jira-Platform-Users
└── VPN-Engineering

🏢 ORGANIZATIONAL GROUPS (3)
├── Mary Smith Directs
├── Platform Engineering Team
└── Engineering-All

📧 DISTRIBUTION LISTS (2)
├── DL-Platform-Announce
└── DL-Engineering-All

📋 OTHER GROUPS (4)
├── Building-A-Access
├── Parking-Reserved
├── Gym-Members
└── Cafeteria-Discount

Total: 14 groups
```

### Step 4: Analyze Membership Paths

**Find relationship path:**
```bash
helios-cli report relationships jdoe@nvidia.com "Platform-Admin-Access"
```

**Visualize nested membership:**
```
Membership Path: John Doe → Platform-Admin-Access
══════════════════════════════════════════

Direct Path Found:
┌─────────────────────────────────────────┐
│ John Doe                                │
│ (jdoe@nvidia.com)                       │
└─────────────────┬───────────────────────┘
                  │ member of
                  ▼
┌─────────────────────────────────────────┐
│ Platform-Engineers                      │
│ (Security Group - 45 members)           │
└─────────────────┬───────────────────────┘
                  │ nested in
                  ▼
┌─────────────────────────────────────────┐
│ Engineering-All                         │
│ (Security Group - 1,200 members)        │
└─────────────────┬───────────────────────┘
                  │ nested in
                  ▼
┌─────────────────────────────────────────┐
│ Platform-Admin-Access                   │
│ (Target Group)                          │
└─────────────────────────────────────────┘

Path Length: 3 hops
Membership Type: Indirect (through nested groups)
```

### Step 5: Bulk Membership Verification

**Check multiple users against one group:**
```bash
#!/bin/bash
# check_team_access.sh

GROUP="Platform-Admin-Access"
USERS=("jdoe@nvidia.com" "asmith@nvidia.com" "bwong@nvidia.com")

echo "Checking $GROUP membership"
echo "════════════════════════════════════"

for user in "${USERS[@]}"; do
  result=$(helios-cli report check-membership "$user" "$GROUP")
  is_member=$(echo "$result" | jq -r '.isMember')
  
  if [ "$is_member" = "true" ]; then
    echo "✅ $user - MEMBER"
  else
    echo "❌ $user - NOT MEMBER"
  fi
done
```

**Check one user against multiple groups:**
```bash
#!/bin/bash
# check_user_access.sh

USER="jdoe@nvidia.com"
GROUPS=("Platform-Admin-Access" "AWS-Admin" "GitHub-Write" "Jira-Admin")

echo "Access check for $USER"
echo "════════════════════════════════════"

for group in "${GROUPS[@]}"; do
  result=$(helios-cli report check-membership "$USER" "$group" 2>/dev/null)
  is_member=$(echo "$result" | jq -r '.isMember' 2>/dev/null)
  
  if [ "$is_member" = "true" ]; then
    echo "✅ $group"
  else
    echo "❌ $group"
  fi
done
```

### Step 6: Report Findings

**Generate access audit report:**
```bash
#!/bin/bash
# access_audit.sh

GROUP="Platform-Admin-Access"
DEPT="Platform Engineering"

echo "Access Audit Report"
echo "Group: $GROUP"
echo "Department: $DEPT"
echo "Generated: $(date)"
echo "════════════════════════════════════"

# Get all department members
helios-cli user search --filter-department "$DEPT" --filter-active true --page-size 500 | jq -r '.[].attributes.email' | while read -r email; do
  result=$(helios-cli report check-membership "$email" "$GROUP" 2>/dev/null)
  is_member=$(echo "$result" | jq -r '.isMember' 2>/dev/null)
  name=$(helios-cli user get "$email" 2>/dev/null | jq -r '.attributes.cn')
  
  echo "$name,$email,$is_member"
done > audit_report.csv

echo ""
echo "Results saved to audit_report.csv"
echo ""
echo "Summary:"
echo "- Total checked: $(wc -l < audit_report.csv)"
echo "- Has access: $(grep -c ',true' audit_report.csv)"
echo "- No access: $(grep -c ',false' audit_report.csv)"
```

## Advanced Scenarios

### Find Users Missing Expected Access

```bash
#!/bin/bash
# Find engineers without GitHub access

DEPT="Platform Engineering"
REQUIRED_GROUP="GitHub-Platform-Team"

echo "Engineers missing $REQUIRED_GROUP access:"
echo "════════════════════════════════════"

helios-cli user search --filter-department "$DEPT" --filter-active true --page-size 500 | jq -r '.[].attributes.email' | while read -r email; do
  result=$(helios-cli report check-membership "$email" "$REQUIRED_GROUP" 2>/dev/null)
  is_member=$(echo "$result" | jq -r '.isMember' 2>/dev/null)
  
  if [ "$is_member" != "true" ]; then
    name=$(helios-cli user get "$email" 2>/dev/null | jq -r '.attributes.cn')
    echo "- $name ($email)"
  fi
done
```

### Audit Privileged Access

```bash
#!/bin/bash
# audit_admin_access.sh

ADMIN_GROUPS=("AWS-Admin" "GCP-Admin" "Platform-Admin" "DB-Admin")

echo "Privileged Access Audit"
echo "════════════════════════════════════"

for group in "${ADMIN_GROUPS[@]}"; do
  echo ""
  echo "Group: $group"
  echo "────────────────────────────────────"
  
  # This would need a different API to list group members
  # Using relationship search as workaround
  helios-cli relationship user-groups --page-size 500 | jq --arg grp "$group" '
    [.[] | select(.attributes.groupName == $grp)] |
    .[].attributes.userDn' -r | while read -r dn; do
      echo "  - $dn"
  done
done
```

### Executive Group Membership Check

```bash
# Check if someone reports to a specific executive
USER="jdoe@nvidia.com"

# Check various "Directs" groups
for exec in "Jensen Huang" "Michael Chen" "Sarah Williams"; do
  group="${exec} Directs"
  result=$(helios-cli report check-membership "$USER" "$group" 2>/dev/null)
  is_member=$(echo "$result" | jq -r '.isMember' 2>/dev/null)
  
  if [ "$is_member" = "true" ]; then
    echo "✅ Reports to $exec"
  fi
done
```

### Compare Team Access Levels

```bash
#!/bin/bash
# Compare access between two teams

TEAM1_MANAGER="msmith"
TEAM2_MANAGER="bjones"
GROUPS=("Platform-Admin-Access" "AWS-Admin" "GitHub-Write")

echo "Team Access Comparison"
echo "════════════════════════════════════"

# Get team members
TEAM1=$(helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$TEAM1_MANAGER" '[.[] | select(.attributes.manager == $mgr) | .attributes.email]')
TEAM2=$(helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$TEAM2_MANAGER" '[.[] | select(.attributes.manager == $mgr) | .attributes.email]')

for group in "${GROUPS[@]}"; do
  t1_count=0
  t2_count=0
  
  for email in $(echo "$TEAM1" | jq -r '.[]'); do
    result=$(helios-cli report check-membership "$email" "$group" 2>/dev/null)
    [ "$(echo "$result" | jq -r '.isMember')" = "true" ] && ((t1_count++))
  done
  
  for email in $(echo "$TEAM2" | jq -r '.[]'); do
    result=$(helios-cli report check-membership "$email" "$group" 2>/dev/null)
    [ "$(echo "$result" | jq -r '.isMember')" = "true" ] && ((t2_count++))
  done
  
  echo "$group:"
  echo "  Team 1 ($TEAM1_MANAGER): $t1_count members"
  echo "  Team 2 ($TEAM2_MANAGER): $t2_count members"
done
```

## Error Handling

### Group Not Found
```
Error: Group "Platform-Admin-Access" not found.

This could mean:
- The group name is misspelled
- The group was renamed or deleted
- You don't have permission to see this group

Try:
- Search for similar names: helios-cli group search --filter-name "Platform"
- Check exact spelling with group owner
- Verify group exists in your organization
```

### User Not Found
```
Error: User "jdoe@nvidia.com" not found.

Please verify:
- Email address is correct
- User is still active at NVIDIA
- Try using login instead: helios-cli report check-membership jdoe "GroupName"
```

### Permission Denied
```
Error: Permission denied to query group membership.

Some security groups restrict membership queries.
Contact the group owner or IT Security for access information.
```

## Example Conversations

### Simple Access Check

**User:** Does John Doe have admin access to Platform?

**Assistant:** Let me check John's Platform admin access...

```
Access Check: John Doe → Platform-Admin-Access
══════════════════════════════════════════

✅ YES - John Doe HAS admin access

Membership Details:
- Type: Nested (indirect)
- Through: Platform-Engineers → Engineering-All → Platform-Admin-Access

This access was granted through his team membership, not directly.
```

### List All Access

**User:** What groups is Alice in?

**Assistant:** Let me retrieve Alice's group memberships...

```
Group Memberships: Alice Wong
══════════════════════════════════════════

🔐 Security/Access (6)
├── Platform-Admin-Access
├── AWS-ReadWrite  
├── GitHub-Platform-Team
├── Kubernetes-Admin
├── Jira-Platform-Users
└── Confluence-Edit

🏢 Organizational (4)
├── Platform Engineering Team
├── Mary Smith Directs
├── Engineering-All
└── Santa Clara Employees

📋 Other (3)
├── DL-Platform-Announce
├── Building-A-Access
└── Gym-Members

Total: 13 groups

Would you like details on any specific group?
```

### Access Audit

**User:** Who on the Platform team doesn't have GitHub access?

**Assistant:** Let me audit GitHub access for Platform Engineering...

```
GitHub Access Audit: Platform Engineering
══════════════════════════════════════════

Checking 45 team members against GitHub-Platform-Team...

❌ Missing Access (3 people):
├── Bob Miller (bmiller@nvidia.com) - Engineer, hired 2025-01-15
├── Carol Davis (cdavis@nvidia.com) - Engineer, hired 2025-02-01
└── David Lee (dlee@nvidia.com) - Contractor

✅ Has Access: 42 people (93%)

Recommendation:
- Bob and Carol are new hires - likely need onboarding
- David is a contractor - may need manager approval

Would you like me to identify who can grant access?
```

## Tips for Group Membership Checks

1. **Know the group name exactly**: Group names are case-sensitive
2. **Check nested membership**: Direct membership isn't the only way
3. **Consider timing**: Group changes may take time to propagate
4. **Audit regularly**: Access should be reviewed periodically
5. **Document findings**: Keep records of access audits
6. **Check both directions**: User→Group and Group→Members
7. **Understand inheritance**: Nested groups can grant unexpected access
