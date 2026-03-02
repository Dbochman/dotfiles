# Verify Group Membership

Check if a user is a member of a specific distribution list.

## Quick Check

```bash
# List all groups a user belongs to
dl-cli memberships user@company.com --toon

# Then search for the target group in the output
```

## Step-by-Step Verification

1. **Get user's group memberships:**
   ```bash
   dl-cli memberships user@company.com --json
   ```

2. **Check if target group is in the list:**
   ```bash
   dl-cli memberships user@company.com --json | jq '.data[] | select(.displayName | test("Engineering"; "i"))'
   ```

3. **For exact match:**
   ```bash
   dl-cli memberships user@company.com --json | jq '.data[] | select(.displayName == "Engineering-All")'
   ```

## Handle Nested Groups

Azure AD groups can be nested. A user might be in "Engineering-All" through membership in "Engineering-Platform".

To check nested membership:

1. **List direct memberships:**
   ```bash
   dl-cli memberships user@company.com --toon
   ```

2. **For each group, check if it's a member of the target:**
   ```bash
   # If user is in "Engineering-Platform", check if that's in "Engineering-All"
   dl-cli members "Engineering-All" --json | jq '.data[] | select(.displayName == "Engineering-Platform")'
   ```

## Notes

- dl-cli shows direct memberships only
- Nested group resolution requires multiple queries
- For complex nested group analysis, consider using Azure Portal or PowerShell
