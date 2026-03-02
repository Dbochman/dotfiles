# Audit Group Membership

List all members of a distribution list with detailed information.

## Basic Audit

```bash
# List all members
dl-cli members "Engineering-All" --toon

# With specific fields
dl-cli members "Engineering-All" --json --fields displayName,mail,jobTitle,department
```

## Export to CSV

```bash
# Export member list to CSV
dl-cli members "Engineering-All" --json | jq -r '
  ["Name","Email","Title","Department"],
  (.data[] | [.displayName, .mail, .jobTitle, .department]) | @csv' > members.csv
```

## Count Members

```bash
# Get member count
dl-cli members "Engineering-All" --json | jq '.metadata.count'
```

## Filter Members

```bash
# Find members from specific domain
dl-cli members "Engineering-All" --json | jq '.data[] | select(.mail | endswith("@nvidia.com"))'

# Find members with specific job title
dl-cli members "Engineering-All" --json | jq '.data[] | select(.jobTitle | test("Engineer"; "i"))'
```

## Compare Groups

```bash
# List members of both groups
dl-cli members "Group-A" --json | jq '[.data[].mail]' > group_a.json
dl-cli members "Group-B" --json | jq '[.data[].mail]' > group_b.json

# Find common members (requires both files)
jq -n --slurpfile a group_a.json --slurpfile b group_b.json '$a[0] - ($a[0] - $b[0])'
```

## Notes

- Large groups may take time to fetch
- Use `--limit` to paginate results if needed
- Member details depend on Azure AD attributes available
