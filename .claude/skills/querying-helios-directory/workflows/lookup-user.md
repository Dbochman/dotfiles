# User Lookup Workflow

This workflow guides you through finding and examining NVIDIA employee profiles using `helios-cli`.

## Workflow Overview

1. Identify the user to look up
2. Retrieve user profile
3. Extract and present relevant information
4. Offer follow-up queries
5. Handle edge cases

## Step-by-Step Process

### Step 1: Identify the User

**Clarify the lookup criteria:**

Users may provide:
- Full email: `jdoe@nvidia.com`
- Login/username: `jdoe`
- Full name: "John Doe" (requires search)
- Partial info: "John from Platform team"

**If only name provided:**
```bash
# Search by name pattern isn't directly supported, so search and filter:
helios-cli user search --filter-active true --page-size 200 | jq '
  .[] | select(.attributes.cn | test("John Doe"; "i")) |
  {name: .attributes.cn, email: .attributes.email, department: .attributes.department}'
```

**If department context provided:**
```bash
helios-cli user search --filter-department "Platform Engineering" --page-size 100 | jq '
  .[] | select(.attributes.cn | test("John"; "i")) |
  {name: .attributes.cn, email: .attributes.email, title: .attributes.title}'
```

**Present to user if multiple matches:**
```
Found multiple matches for "John":

1. John Doe (jdoe@nvidia.com) - Senior Engineer, Platform Engineering
2. John Smith (jsmith@nvidia.com) - Manager, Cloud Services
3. Johnny Nguyen (jnguyen@nvidia.com) - Engineer, AI Platform

Which person are you looking for? (Enter number or provide more details)
```

### Step 2: Retrieve User Profile

Once you have the identifier:

```bash
helios-cli user get jdoe@nvidia.com
```

**Extract key information:**
```bash
helios-cli user get jdoe@nvidia.com | jq '{
  name: .attributes.cn,
  email: .attributes.email,
  login: .attributes.login,
  title: .attributes.title,
  department: .attributes.department,
  manager: .attributes.manager,
  site: .attributes.site,
  country: .attributes.country,
  employmentType: .attributes.employmentType,
  hireDate: .attributes.hireDate,
  isManager: .attributes.isManager,
  directReports: .attributes.childUserCount
}'
```

### Step 3: Present Information

Format the profile in a clear, readable way:

```
Employee Profile: John Doe
══════════════════════════════════════════

📧 Email:       jdoe@nvidia.com
👤 Login:       jdoe
💼 Title:       Senior Software Engineer
🏢 Department:  Platform Engineering
👔 Manager:     Mary Smith (msmith)
📍 Location:    Santa Clara, US
📅 Hire Date:   January 15, 2020
👥 Type:        Full-Time Employee (FTE)

Management:
├── Is Manager: No
└── Direct Reports: 0

Would you like to:
  [M] View manager chain
  [T] View team members (colleagues)
  [G] Check group memberships
  [P] Download profile photo
  [C] View contact details
  [S] Search for another person
```

### Step 4: Handle Follow-up Queries

**[M] View Manager Chain:**
```bash
helios-cli user get jdoe@nvidia.com | jq -r '.attributes.managerChain[] | "\(.level). \(.name) (\(.login))"'
```

Present as:
```
Manager Chain for John Doe:
1. Mary Smith (msmith) - Direct Manager
2. Robert Johnson (rjohnson) - Director
3. Sarah Williams (swilliams) - VP Engineering
4. Michael Chen (mchen) - SVP
5. Jensen Huang (jhuang) - CEO
```

**[T] View Team Members:**
```bash
MANAGER=$(helios-cli user get jdoe@nvidia.com | jq -r '.attributes.manager')
helios-cli user search --filter-active true --page-size 200 | jq --arg mgr "$MANAGER" '
  [.[] | select(.attributes.manager == $mgr)] |
  sort_by(.attributes.cn) |
  .[] | {name: .attributes.cn, email: .attributes.email, title: .attributes.title}'
```

**[G] Check Group Memberships:**
```bash
helios-cli relationship user-groups --page-size 100 | jq '
  .[] | select(.attributes.userDn | test("John Doe"; "i")) |
  .attributes.groupName'
```

**[P] Download Profile Photo:**
```bash
helios-cli user get jdoe@nvidia.com | jq -r '.attributes.base64Photo' | base64 -d > jdoe_photo.jpg
echo "Photo saved to jdoe_photo.jpg"
```

**[C] View Contact Details:**
```bash
helios-cli user get jdoe@nvidia.com | jq '{
  email: .attributes.email,
  officePhone: .attributes.officePhone,
  mobilePhone: .attributes.mobilePhone,
  site: .attributes.site
}'
```

### Step 5: Handle Edge Cases

**User Not Found:**
```
User 'jdoe@nvidia.com' not found.

This could mean:
- The email/login is misspelled
- The user has left NVIDIA
- The user is using a different email domain

Would you like to:
  [S] Search by partial name
  [D] Search by department
  [R] Retry with different identifier
```

**Inactive User:**
If user exists but appears inactive:
```bash
helios-cli user get jdoe@nvidia.com | jq '{
  name: .attributes.cn,
  active: .attributes.isActive,
  lastModified: .attributes.modifyTimestamp
}'
```

Present as:
```
⚠️ This user may be inactive or has left NVIDIA.

Last known info:
- Name: John Doe
- Status: Inactive
- Last Updated: 2024-06-15

Would you like to search for their replacement or manager?
```

**Multiple Email Domains:**
NVIDIA employees may have different email domains:
- `@nvidia.com` - Primary
- `@exchange.nvidia.com` - Exchange alias

Try alternate if primary fails:
```bash
# If jdoe@nvidia.com fails, try login
helios-cli user get jdoe
```

## Advanced Lookup Scenarios

### Lookup by Phone Number
Not directly supported. Search and filter:
```bash
helios-cli user search --filter-active true --page-size 500 | jq '
  .[] | select(.attributes.officePhone | test("408-555-1234")) |
  {name: .attributes.cn, email: .attributes.email}'
```

### Lookup Recent Hires
```bash
helios-cli user search --filter-active true --page-size 500 | jq --arg date "2025-01-01" '
  [.[] | select(.attributes.hireDate > $date)] |
  sort_by(.attributes.hireDate) | reverse |
  .[:10] | .[] | {name: .attributes.cn, hireDate: .attributes.hireDate, department: .attributes.department}'
```

### Compare Two Users
```bash
# Get both profiles
USER1=$(helios-cli user get user1@nvidia.com)
USER2=$(helios-cli user get user2@nvidia.com)

# Compare managers
echo "User 1 manager: $(echo $USER1 | jq -r '.attributes.manager')"
echo "User 2 manager: $(echo $USER2 | jq -r '.attributes.manager')"

# Check if they share a manager
SHARED=$(echo $USER1 $USER2 | jq -s '
  if .[0].attributes.manager == .[1].attributes.manager then "Yes - same manager"
  else "No - different managers"
  end')
```

### Find User's Skip-Level Manager
```bash
helios-cli user get jdoe@nvidia.com | jq -r '.attributes.managerChain[1] | "\(.name) (\(.login))"'
```

## Error Handling

### Authentication Errors
```
Error: unauthorized

Your API token may be invalid or expired.
Please run: helios-cli auth login
Get a new token from: https://nvidia.atlassian.net/wiki/spaces/HELIOS/pages/2336096273
```

### Network Errors
```
Error: network_error

Unable to connect to Helios API. Please check:
1. You're connected to NVIDIA VPN
2. Your network connection is stable
3. The Helios service is operational

Retry? [Y/N]
```

### Rate Limiting
```
Error: rate_limit

Too many requests. The API has rate limits to prevent abuse.
Please wait 60 seconds before retrying.

[Waiting 60 seconds...]
```

## Example Conversations

### Simple Lookup

**User:** Who is jdoe@nvidia.com?

**Assistant:** Let me look up that employee...

```
Employee Profile: John Doe
══════════════════════════════════════════

📧 Email:       jdoe@nvidia.com
💼 Title:       Senior Software Engineer
🏢 Department:  Platform Engineering
👔 Manager:     Mary Smith
📍 Location:    Santa Clara, US

Would you like more details? [M]anager chain, [T]eam, [G]roups, [C]ontact info
```

### Name-Based Lookup

**User:** Find someone named Sarah in the AI team

**Assistant:** Let me search for Sarah in AI-related departments...

```
Found 3 matches:

1. Sarah Chen (schen@nvidia.com)
   - Principal Engineer, AI Platform
   - Santa Clara, US

2. Sarah Williams (swilliams@nvidia.com)
   - VP, AI Research
   - Seattle, US

3. Sarah Kumar (skumar@nvidia.com)
   - Engineer, AI Infrastructure
   - Bangalore, IN

Which Sarah are you looking for?
```

### Detailed Investigation

**User:** I need full details on msmith including her team and reporting structure

**Assistant:** Let me gather comprehensive information on Mary Smith...

```
Employee Profile: Mary Smith
══════════════════════════════════════════

📧 Email:       msmith@nvidia.com
💼 Title:       Engineering Manager
🏢 Department:  Platform Engineering
📍 Location:    Santa Clara, US
📅 Tenure:      5 years (hired Jan 2020)
👥 Type:        FTE

Management Status: ✓ Is a Manager
Direct Reports: 8

Manager Chain:
1. Robert Johnson (Director)
2. Sarah Williams (VP Engineering)
3. Michael Chen (SVP)
4. Jensen Huang (CEO)

Team Members (8):
- John Doe - Senior Engineer
- Alice Wang - Staff Engineer
- Bob Miller - Engineer
- [... 5 more ...]

Would you like to see group memberships or export this data?
```

## Tips for Effective Lookups

1. **Start with email if known**: Most reliable identifier
2. **Use login as fallback**: Works when email format is uncertain
3. **Search by department**: Helps narrow down when only name is known
4. **Check manager chain**: Useful for understanding reporting relationships
5. **Export photos early**: Photo data is large, extract only when needed
6. **Cache results**: If doing multiple queries on same user, store the result
