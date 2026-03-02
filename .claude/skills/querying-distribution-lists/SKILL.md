---
name: querying-distribution-lists
description: Query Microsoft 365 distribution lists and group memberships via CLI. Lists groups, views members, and checks user memberships. Use when checking group membership, finding distribution list members, listing groups a user belongs to, or querying Azure AD groups.
---
<!--
Progressive Disclosure:
- Level 1 (YAML front matter): Skill metadata
- Level 2 (This file): Overview, quick start
- Level 3: workflows/ for detailed procedures

Related skills:
- querying-helios-directory: For NVIDIA LDAP queries (different data source)
- managing-outlook-email: For email operations
-->

# Distribution List Queries

Query Microsoft 365 distribution lists and group memberships via `dl-cli`.

**Note:** This is a read-only tool. Group management is handled by GroupID Self-Service portal.

## Verify Installation

```bash
# Check tool is available
dl-cli --version

# Test authentication (will prompt for login if needed)
dl-cli find --limit 1 --toon
```

If command not found, see [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/).

For NVIDIA LDAP queries (employee directory, org charts), see [querying-helios-directory](../querying-helios-directory/SKILL.md).

## When to Use This Skill

Use this skill when users want to:

- **List groups**: See distribution lists they own or belong to
- **View members**: List members of a distribution list
- **Check membership**: Find what groups a user belongs to
- **Search groups**: Find groups by name or description
- **Get group details**: View group properties and metadata

## Quick Start Examples

### List Your Groups
```bash
# Groups you own or are a member of
dl-cli find --toon

# Search for specific groups
dl-cli search "Engineering" --toon
```

### View Group Members
```bash
# By group name
dl-cli members "Engineering-All" --toon

# By email address
dl-cli members engineering-all@company.com --toon

# With specific fields
dl-cli members "Engineering-All" --json --fields mail,displayName,jobTitle
```

### Check User Memberships
```bash
# What groups does a user belong to?
dl-cli memberships user@company.com --toon
```

### Get Group Details
```bash
# Full group information
dl-cli get "Engineering-All" --toon
```

Run `dl-cli --help` for all commands and flags. Run `dl-cli <command> --help` for detailed options.

## Workflows

1. **Verify Membership** ([workflows/verify-membership.md](workflows/verify-membership.md))
   - Check if a user is in a specific group
   - Handle nested group memberships

2. **Audit Group** ([workflows/audit-group.md](workflows/audit-group.md))
   - List all members with details
   - Export membership data

## Troubleshooting

**Authentication errors:**
```bash
rm ~/.ai-pim-utils/auth-cache
dl-cli find --limit 1  # Triggers re-authentication
```

**Permission denied:**
- Some groups have restricted membership visibility
- You can only see groups you own or are a member of

**Multiple groups found:**
- Use more specific identifier: full display name (in quotes), email address, or object ID
- Example: `dl-cli get "Engineering-Platform-Team"` instead of `dl-cli get Engineering`

**Group not found:**
- Verify spelling (names are case-sensitive)
- Try searching: `dl-cli search "partial name" --toon`

**See:** [installation page](https://outlook-cli-80d21a.gitlab-master-pages.nvidia.com/) for detailed troubleshooting.
