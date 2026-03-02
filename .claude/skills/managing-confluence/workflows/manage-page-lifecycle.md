# Page Lifecycle Management Workflow

This workflow guides you through the complete lifecycle of Confluence pages - from creation through updates, versioning, review, and archival using `confluence-cli`.

## Workflow Overview

1. Understand page state and goals
2. Review current version and history
3. Make updates with proper versioning
4. Manage labels and metadata
5. Handle reviews and approvals
6. Archive or deprecate as needed

## Step-by-Step Process

### Step 1: Understand Page State

**Check current page status:**
```bash
PAGE_ID="12345"

# Get page details
confluence-cli page get "$PAGE_ID" --expand version --json | jq '{
  id: .id,
  title: .title,
  space: .space.key,
  version: .version.number,
  lastModified: .version.when,
  lastModifiedBy: .version.by.displayName
}'
```

**Check labels for status:**
```bash
confluence-cli label list "$PAGE_ID" --json | jq -r '.[] | .name'
```

**Present page status:**
```
Page Status: API Documentation
══════════════════════════════════════════

📄 Title: API Documentation
📁 Space: ENG
🔢 Version: 15
📅 Last Modified: 2025-01-15 10:30:00
👤 Modified By: John Doe

🏷️ Labels:
├── api-docs (category)
├── reviewed (status)
├── v2 (version)
└── platform-team (owner)

📊 Status: Active and reviewed

What would you like to do?
1. Update content
2. Review version history
3. Change labels/status
4. Archive this page
```

### Step 2: Review Version History

**List version history:**
```bash
PAGE_ID="12345"

confluence-cli version list "$PAGE_ID" --limit 10 --json | jq '.[] | {
  version: .number,
  when: .when,
  by: .by.displayName,
  message: .message
}'
```

**Compare versions:**
```bash
# Get specific version content
confluence-cli version get "$PAGE_ID" 10 --format markdown > version_10.md
confluence-cli version get "$PAGE_ID" 15 --format markdown > version_15.md

# Diff versions
diff version_10.md version_15.md
```

**Present version history:**
```
Version History: API Documentation
══════════════════════════════════════════

Version  Date        Author       Notes
───────────────────────────────────────────
15       2025-01-15  John Doe     Updated authentication section
14       2025-01-10  Jane Smith   Fixed typos
13       2025-01-05  John Doe     Added rate limiting docs
12       2024-12-20  Bob Wilson   Restructured endpoints
11       2024-12-15  John Doe     Initial v2 documentation
...

Showing 5 of 15 versions.

Would you like to:
1. View a specific version
2. Compare two versions
3. Restore an older version
4. Continue with latest version
```

### Step 3: Update Content

**Simple content update:**
```bash
PAGE_ID="12345"

confluence-cli page update "$PAGE_ID" \
  --body "# API Documentation

## Overview

Updated content here...

## Authentication

New authentication details..." \
  --format markdown
```

**Update with version message:**
```bash
# Get current version first
CURRENT_VERSION=$(confluence-cli page get "$PAGE_ID" --json | jq '.version.number')

confluence-cli page update "$PAGE_ID" \
  --body "$(cat updated_content.md)" \
  --format markdown \
  --version "$CURRENT_VERSION"
```

**Safe update workflow:**
```bash
#!/bin/bash
# safe-update.sh - Update with conflict detection

PAGE_ID="$1"
NEW_CONTENT="$2"

# Get current version
CURRENT=$(confluence-cli page get "$PAGE_ID" --json)
CURRENT_VERSION=$(echo "$CURRENT" | jq '.version.number')
CURRENT_TITLE=$(echo "$CURRENT" | jq -r '.title')

echo "Updating: $CURRENT_TITLE"
echo "Current version: $CURRENT_VERSION"

# Attempt update with version check
if confluence-cli page update "$PAGE_ID" \
     --body "$NEW_CONTENT" \
     --format markdown \
     --version "$CURRENT_VERSION" 2>/dev/null; then
    
    NEW_VERSION=$((CURRENT_VERSION + 1))
    echo "✅ Updated successfully to version $NEW_VERSION"
else
    echo "❌ Update failed - page may have been modified"
    echo "Please review latest version and retry"
    exit 1
fi
```

**Append to existing content:**
```bash
#!/bin/bash
# append-to-page.sh - Add content without replacing

PAGE_ID="$1"
APPEND_CONTENT="$2"

# Get existing content
EXISTING=$(confluence-cli page get "$PAGE_ID" --format markdown)

# Combine
NEW_CONTENT="$EXISTING

$APPEND_CONTENT"

# Update
confluence-cli page update "$PAGE_ID" --body "$NEW_CONTENT" --format markdown
```

### Step 4: Manage Labels and Metadata

**Update status labels:**
```bash
PAGE_ID="12345"

# Remove old status label
confluence-cli label remove "$PAGE_ID" --label "draft"

# Add new status label
confluence-cli label add "$PAGE_ID" --label "reviewed"
confluence-cli label add "$PAGE_ID" --label "approved"
```

**Label lifecycle workflow:**
```bash
#!/bin/bash
# update-page-status.sh - Update page status through labels

PAGE_ID="$1"
NEW_STATUS="$2"

STATUSES=("draft" "in-review" "reviewed" "approved" "deprecated")

# Remove existing status labels
for status in "${STATUSES[@]}"; do
    confluence-cli label remove "$PAGE_ID" --label "$status" 2>/dev/null
done

# Add new status
confluence-cli label add "$PAGE_ID" --label "$NEW_STATUS"

echo "Updated page $PAGE_ID status to: $NEW_STATUS"
```

**Add version label:**
```bash
# Mark page version
confluence-cli label add "$PAGE_ID" --label "v2"
confluence-cli label remove "$PAGE_ID" --label "v1"
```

### Step 5: Handle Reviews

**Request review workflow:**
```bash
#!/bin/bash
# request-review.sh - Mark page for review

PAGE_ID="$1"
REVIEWER="$2"

# Update status label
confluence-cli label remove "$PAGE_ID" --label "draft"
confluence-cli label add "$PAGE_ID" --label "in-review"

# Add review comment
confluence-cli comment add "$PAGE_ID" --body "
<p><strong>Review Requested</strong></p>
<p>Reviewer: $REVIEWER</p>
<p>Requested: $(date)</p>
<p>Please review and update status label when complete.</p>
"

echo "Review requested for page $PAGE_ID"
echo "Reviewer: $REVIEWER"
```

**Complete review workflow:**
```bash
#!/bin/bash
# complete-review.sh - Mark review as complete

PAGE_ID="$1"
REVIEWER="$2"
APPROVED="${3:-yes}"  # yes or no

# Update status
confluence-cli label remove "$PAGE_ID" --label "in-review"

if [ "$APPROVED" = "yes" ]; then
    confluence-cli label add "$PAGE_ID" --label "approved"
    STATUS_MSG="✅ Approved"
else
    confluence-cli label add "$PAGE_ID" --label "needs-changes"
    STATUS_MSG="⚠️ Changes Requested"
fi

# Add review comment
confluence-cli comment add "$PAGE_ID" --body "
<p><strong>Review Complete</strong></p>
<p>Reviewer: $REVIEWER</p>
<p>Date: $(date)</p>
<p>Status: $STATUS_MSG</p>
"

echo "Review complete: $STATUS_MSG"
```

**Bulk review status check:**
```bash
#!/bin/bash
# check-review-status.sh - Find pages pending review

echo "Pages Pending Review"
echo "════════════════════════════════════════"

confluence-cli search "label = 'in-review'" --type page --json | \
  jq -r '.results[] | "\(.id)|\(.title)|\(.space.key)"' | while IFS='|' read -r id title space; do
    # Get last comment (might be review request)
    COMMENTS=$(confluence-cli comment list "$id" --limit 1 --json 2>/dev/null)
    REQUESTER=$(echo "$COMMENTS" | jq -r '.[0].body.storage.value' | grep -oP 'Reviewer: \K[^<]+' || echo "Unknown")
    
    echo "📄 $title"
    echo "   Space: $space | ID: $id"
    echo "   Reviewer: $REQUESTER"
    echo ""
done
```

### Step 6: Archive and Deprecate

**Mark page as deprecated:**
```bash
#!/bin/bash
# deprecate-page.sh - Mark page as deprecated

PAGE_ID="$1"
REPLACEMENT_ID="$2"  # Optional: ID of replacement page

# Add deprecation labels
confluence-cli label add "$PAGE_ID" --label "deprecated"
confluence-cli label remove "$PAGE_ID" --label "approved"
confluence-cli label remove "$PAGE_ID" --label "current"

# Get current content
CONTENT=$(confluence-cli page get "$PAGE_ID" --format markdown)

# Add deprecation notice
if [ -n "$REPLACEMENT_ID" ]; then
    REPLACEMENT_TITLE=$(confluence-cli page get "$REPLACEMENT_ID" --json | jq -r '.title')
    NOTICE="> ⚠️ **DEPRECATED**: This page is no longer maintained. Please see [$REPLACEMENT_TITLE](/pages/$REPLACEMENT_ID) for current information."
else
    NOTICE="> ⚠️ **DEPRECATED**: This page is no longer maintained and may contain outdated information."
fi

NEW_CONTENT="$NOTICE

$CONTENT"

# Update page
confluence-cli page update "$PAGE_ID" --body "$NEW_CONTENT" --format markdown

echo "Page $PAGE_ID marked as deprecated"
```

**Archive page hierarchy:**
```bash
#!/bin/bash
# archive-hierarchy.sh - Archive a page and all children

PARENT_ID="$1"

archive_page() {
    local id=$1
    
    # Add archive label
    confluence-cli label add "$id" --label "archived"
    confluence-cli label add "$id" --label "$(date +%Y-%m)"  # Archive date
    
    echo "Archived: $id"
}

# Archive parent
archive_page "$PARENT_ID"

# Archive all children
confluence-cli search "ancestor=$PARENT_ID AND type=page" --json | \
  jq -r '.results[].id' | while read -r child_id; do
    archive_page "$child_id"
done

echo "Archive complete"
```

**Find and clean old pages:**
```bash
#!/bin/bash
# find-stale-pages.sh - Find pages that may need archival

DAYS="${1:-365}"  # Default: 1 year

echo "Pages not modified in $DAYS days"
echo "════════════════════════════════════════"

confluence-cli search "type=page AND lastModified <= now('-${DAYS}d') AND label != 'archived'" --limit 100 --json | \
  jq -r '.results[] | "\(.id)|\(.title)|\(.space.key)|\(.version.when)"' | while IFS='|' read -r id title space modified; do
    
    echo "📄 $title"
    echo "   Space: $space"
    echo "   Last modified: $modified"
    echo "   ID: $id"
    echo ""
done

echo ""
echo "Consider archiving these pages or updating them."
```

## Advanced Scenarios

### Bulk Update Multiple Pages

```bash
#!/bin/bash
# bulk-update.sh - Update multiple pages with same change

SEARCH_QUERY="$1"
PREPEND_TEXT="$2"

echo "Finding pages..."
PAGES=$(confluence-cli search "$SEARCH_QUERY" --type page --json)
COUNT=$(echo "$PAGES" | jq '.results | length')

echo "Found $COUNT pages to update"
read -p "Continue? [y/N] " confirm
[ "$confirm" != "y" ] && exit 0

echo "$PAGES" | jq -r '.results[].id' | while read -r id; do
    TITLE=$(confluence-cli page get "$id" --json | jq -r '.title')
    echo "Updating: $TITLE"
    
    CONTENT=$(confluence-cli page get "$id" --format markdown)
    NEW_CONTENT="$PREPEND_TEXT

$CONTENT"
    
    confluence-cli page update "$id" --body "$NEW_CONTENT" --format markdown
done

echo "Bulk update complete"
```

### Scheduled Content Review

```bash
#!/bin/bash
# schedule-review.sh - Find pages due for periodic review

REVIEW_INTERVAL_DAYS="${1:-90}"

echo "Pages due for review (not updated in $REVIEW_INTERVAL_DAYS days)"
echo "════════════════════════════════════════"

# Find pages with 'requires-periodic-review' label
confluence-cli search "label = 'requires-periodic-review' AND lastModified <= now('-${REVIEW_INTERVAL_DAYS}d')" --json | \
  jq -r '.results[] | "\(.id)|\(.title)|\(.version.when)"' | while IFS='|' read -r id title modified; do
    
    # Mark for review
    confluence-cli label add "$id" --label "review-due"
    
    echo "📋 $title"
    echo "   Last updated: $modified"
    echo "   Marked for review"
    echo ""
done
```

### Version Rollback

```bash
#!/bin/bash
# rollback-page.sh - Restore page to previous version

PAGE_ID="$1"
TARGET_VERSION="$2"

if [ -z "$PAGE_ID" ] || [ -z "$TARGET_VERSION" ]; then
    echo "Usage: $0 <page-id> <target-version>"
    exit 1
fi

echo "Rolling back page $PAGE_ID to version $TARGET_VERSION"

# Get old version content
OLD_CONTENT=$(confluence-cli version get "$PAGE_ID" "$TARGET_VERSION" --format markdown)

if [ -z "$OLD_CONTENT" ]; then
    echo "Failed to retrieve version $TARGET_VERSION"
    exit 1
fi

# Update page with old content
confluence-cli page update "$PAGE_ID" --body "$OLD_CONTENT" --format markdown

echo "✅ Rollback complete"
echo "Page restored to version $TARGET_VERSION content"
echo "Note: This creates a new version (not a true revert)"
```

## Error Handling

### Concurrent Edit Conflict
```
Error: Conflict - page was modified by another user.

The page has been updated since you started editing.
Current version: 16 (your version: 15)

Options:
1. Fetch latest version and merge changes manually
2. Force overwrite (may lose other user's changes)
3. Cancel update

To merge manually:
  confluence-cli page get 12345 --format markdown > latest.md
  # Manually merge your changes
  confluence-cli page update 12345 --body "$(cat merged.md)" --format markdown
```

### Page Locked
```
Error: Page is locked for editing.

The page is currently being edited by: Jane Smith
Lock expires: 2025-01-15 11:30:00

Options:
1. Wait for lock to expire
2. Contact Jane Smith to release lock
3. Try again later
```

### Permission Changes
```
Error: Permission denied.

You no longer have edit permission for this page.
This may be due to:
- Space permission changes
- Page-level restrictions
- Your role changes

Contact the space administrator for access.
```

## Example Conversations

### Simple Update

**User:** Update the API docs to add a new endpoint

**Assistant:** I'll help you update the API documentation. First, let me get the current page...

```
Current Page: API Documentation
══════════════════════════════════════════

Version: 15
Last modified: 2025-01-15 by John Doe
Status: Approved ✓

I'll append the new endpoint to the existing content.
Please provide the endpoint details:
- Endpoint path (e.g., GET /users/{id})
- Description
- Request/response format
```

**User:** POST /webhooks - Creates a new webhook subscription. Request body has url and events array.

**Assistant:** Adding the new endpoint...

```
Update Complete!
══════════════════════════════════════════

✅ Added new endpoint: POST /webhooks
📄 Page: API Documentation
🔢 New version: 16

Changes made:
- Added POST /webhooks section
- Included request body schema
- Added example request/response

Status labels updated:
- Removed: approved
- Added: in-review

The page should be reviewed before re-approval.
Would you like to request a review from someone?
```

### Lifecycle Management

**User:** Archive all the v1 documentation since we've moved to v2

**Assistant:** I'll help archive the v1 documentation. Let me find all v1 pages...

```
V1 Documentation Found
══════════════════════════════════════════

Found 12 pages with label 'v1':

├── API v1 Overview
├── Authentication (v1)
├── Endpoints Reference (v1)
├── [9 more pages...]

Archive Plan:
1. Add 'archived' and 'v1-archived' labels
2. Add deprecation notice pointing to v2 docs
3. Remove from main navigation (if applicable)

⚠️ This will affect 12 pages.

Proceed with archive? [y/N]
```

## Tips for Page Lifecycle Management

1. **Use labels consistently**: Define status labels (draft, reviewed, approved, deprecated)
2. **Version before major changes**: Note the version number before significant updates
3. **Add change comments**: Document why changes were made
4. **Review periodically**: Set up processes for regular content review
5. **Archive don't delete**: Keep old content accessible but clearly marked
6. **Communicate changes**: Notify stakeholders of significant updates
7. **Test in draft**: Use draft status for work-in-progress content
8. **Document deprecation**: Always point to replacement content
