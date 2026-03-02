---
name: querying-helios-directory
description: Query NVIDIA employee directory via Helios LDAP API. Retrieves user profiles, org charts, manager chains, group memberships, and team structures. Use when looking up employees, finding reporting relationships, checking group membership, or analyzing org structure at NVIDIA.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata and description
- Level 2 (This file): Overview, quick start, common commands
- Level 3: workflows/, examples/

Shared resources (in managing-outlook-email):
- Installation: https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/

-->

# NVIDIA Employee Directory (Helios)

Query NVIDIA's LDAP directory for employee information, org structure, and group memberships via `helios-cli`.

## Verify Installation

```bash
# Check tool is available
helios-cli --version

# Check authentication status
helios-cli auth status
```

If not authenticated, run `helios-cli auth login` with your API token from https://nvidia.atlassian.net/wiki/spaces/HELIOS/pages/2336096273

**Prerequisites:**
- NVIDIA network access (VPN if remote)
- Valid Helios API token

## When to Use This Skill

Use this skill when users want to:

- **Look up employees**: Find someone by email, login, or name
- **Explore org structure**: View manager chains, direct reports, team hierarchies
- **Check group membership**: Verify if someone belongs to a security group or team
- **Find team members**: List everyone in a department or reporting to a manager
- **Analyze org data**: Count employees by department, location, or employment type
- **Export team rosters**: Generate CSV exports of team data

## Quick Start Examples

### Find a User
```bash
# By email
helios-cli user get jdoe@nvidia.com

# By login (username)
helios-cli user get jdoe

# Get specific fields with jq
helios-cli user get jdoe@nvidia.com | jq '{name: .attributes.cn, title: .attributes.title, manager: .attributes.manager}'
```

### View Org Structure
```bash
# Get someone's full management chain
helios-cli user get jdoe@nvidia.com | jq '.attributes.managerChain[].name'

# Check if user reports to an executive (directly or indirectly)
helios-cli report check-membership jdoe@nvidia.com "Jensen Huang Directs"

# Find relationship path between user and group
helios-cli report relationships jdoe@nvidia.com "Some Security Group"
```

### Search Users
```bash
# Find users in a department
helios-cli user search --filter-department "Engineering" --page-size 100

# Find all active users
helios-cli user search --filter-active true --all

# List all unique departments
helios-cli report unique-values department --type user
```

### Work with Groups
```bash
# Get group details
helios-cli group get "Engineering Team"

# Search groups by name
helios-cli group search --filter-name "Platform"

# Check if user is in a group
helios-cli report check-membership jdoe "Platform-Admin-Access"
```

### Export Team Data
```bash
# Export department to CSV
helios-cli user search --filter-department "Platform Engineering" --filter-active true --page-size 500 | jq -r '
  ["Name","Email","Title","Manager","Site"],
  (.[] | [.attributes.cn, .attributes.email, .attributes.title, .attributes.manager, .attributes.site]) | @csv'
```

Run `helios-cli --help` for all commands, flags, and jq examples. Run `helios-cli <command> --help` for detailed options.

## Workflows

1. **User Lookup** ([workflows/lookup-user.md](workflows/lookup-user.md))
   - Find users by email, login, or name
   - View profile details and manager chain
   - Handle edge cases and follow-up queries

2. **Org Exploration** ([workflows/explore-org.md](workflows/explore-org.md))
   - Navigate org hierarchies up and down
   - Find connections between people
   - Visualize team structures

3. **Team Analysis** ([workflows/team-analysis.md](workflows/team-analysis.md))
   - Compute headcount and metrics
   - Analyze tenure, geography, composition
   - Export team rosters

4. **Group Membership** ([workflows/group-membership.md](workflows/group-membership.md))
   - Verify security group access
   - Audit team permissions
   - Understand nested memberships

## Example Scripts

Ready-to-use shell scripts for common tasks:

- **[examples/lookup-user.sh](examples/lookup-user.sh)** - Look up and display user profile
- **[examples/org-chart.sh](examples/org-chart.sh)** - Generate org chart for a manager
- **[examples/team-export.sh](examples/team-export.sh)** - Export team data to CSV

## Troubleshooting

**Authentication fails:**
```bash
helios-cli auth logout
helios-cli auth login  # Re-enter token
```

**Command not found:**
- Verify `helios-cli` is in PATH
- Check installation: `which helios-cli`
- See [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/)

**No results returned:**
- Check filter spelling (case-sensitive)
- Try broader search without filters
- Verify network connectivity (NVIDIA VPN)

**Rate limiting:**
- Reduce page size
- Add filters to narrow results
- Wait between large queries
