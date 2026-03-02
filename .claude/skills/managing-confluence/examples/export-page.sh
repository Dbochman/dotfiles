#!/bin/bash
# export-page.sh - Export a Confluence page to markdown
#
# Usage: ./export-page.sh <page-id> [output-file]
# Example: ./export-page.sh 12345
#          ./export-page.sh 12345 my-doc.md

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <page-id> [output-file]"
    echo "Example: $0 12345"
    echo "         $0 12345 my-document.md"
    exit 1
fi

PAGE_ID="$1"

echo "Fetching page $PAGE_ID..."

# Get page metadata
PAGE_INFO=$(confluence-cli page get "$PAGE_ID" --json)

if [ -z "$PAGE_INFO" ] || echo "$PAGE_INFO" | grep -q "not_found"; then
    echo "❌ Page not found: $PAGE_ID"
    exit 1
fi

TITLE=$(echo "$PAGE_INFO" | jq -r '.title')
SPACE=$(echo "$PAGE_INFO" | jq -r '.space.key')
VERSION=$(echo "$PAGE_INFO" | jq -r '.version.number')
MODIFIED=$(echo "$PAGE_INFO" | jq -r '.version.when')
AUTHOR=$(echo "$PAGE_INFO" | jq -r '.version.by.displayName')

# Generate output filename if not provided
if [ -z "$2" ]; then
    OUTPUT_FILE=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-').md
else
    OUTPUT_FILE="$2"
fi

echo "Exporting: $TITLE"
echo "To: $OUTPUT_FILE"

# Export with YAML frontmatter
{
    echo "---"
    echo "title: \"$TITLE\""
    echo "page_id: $PAGE_ID"
    echo "space: $SPACE"
    echo "version: $VERSION"
    echo "last_modified: \"$MODIFIED\""
    echo "author: \"$AUTHOR\""
    echo "exported: \"$(date -Iseconds)\""
    echo "source: \"https://company.atlassian.net/wiki/spaces/$SPACE/pages/$PAGE_ID\""
    echo "---"
    echo ""
    confluence-cli page get "$PAGE_ID" --format markdown
} > "$OUTPUT_FILE"

echo ""
echo "════════════════════════════════════════"
echo "✅ Export complete!"
echo ""
echo "📄 Title: $TITLE"
echo "📁 Space: $SPACE"
echo "🔢 Version: $VERSION"
echo "📅 Modified: $MODIFIED"
echo "👤 Author: $AUTHOR"
echo ""
echo "📝 Output: $OUTPUT_FILE"
echo "📊 Size: $(wc -c < "$OUTPUT_FILE") bytes"
