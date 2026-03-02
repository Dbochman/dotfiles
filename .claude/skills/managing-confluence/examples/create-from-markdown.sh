#!/bin/bash
# create-from-markdown.sh - Create a Confluence page from a markdown file
#
# Usage: ./create-from-markdown.sh <space-key> <markdown-file> [parent-page-id]
# Example: ./create-from-markdown.sh DOCS readme.md
#          ./create-from-markdown.sh DOCS guide.md 12345

set -e

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: $0 <space-key> <markdown-file> [parent-page-id]"
    echo ""
    echo "Arguments:"
    echo "  space-key      - Confluence space key (e.g., DOCS, ENG)"
    echo "  markdown-file  - Path to markdown file"
    echo "  parent-page-id - Optional parent page ID for hierarchy"
    echo ""
    echo "Examples:"
    echo "  $0 DOCS readme.md"
    echo "  $0 ENG api-guide.md 12345"
    exit 1
fi

SPACE="$1"
MD_FILE="$2"
PARENT_ID="${3:-}"

if [ ! -f "$MD_FILE" ]; then
    echo "❌ File not found: $MD_FILE"
    exit 1
fi

# Extract title from first heading or use filename
TITLE=$(head -20 "$MD_FILE" | grep -m1 "^#\s" | sed 's/^#\s*//')
if [ -z "$TITLE" ]; then
    TITLE=$(basename "$MD_FILE" .md)
fi

echo "Creating Confluence page..."
echo "════════════════════════════════════════"
echo "📄 Title: $TITLE"
echo "📁 Space: $SPACE"
[ -n "$PARENT_ID" ] && echo "📂 Parent: $PARENT_ID"
echo "📝 Source: $MD_FILE"
echo ""

# Build create command
CREATE_CMD="confluence-cli page create --space \"$SPACE\" --title \"$TITLE\" --body \"\$(cat \"$MD_FILE\")\" --format markdown"

if [ -n "$PARENT_ID" ]; then
    CREATE_CMD="$CREATE_CMD --parent \"$PARENT_ID\""
fi

CREATE_CMD="$CREATE_CMD --json"

# Execute and capture result
RESULT=$(eval "$CREATE_CMD")

if echo "$RESULT" | grep -q '"id"'; then
    PAGE_ID=$(echo "$RESULT" | jq -r '.id')
    PAGE_URL=$(echo "$RESULT" | jq -r '._links.webui')
    
    echo "✅ Page created successfully!"
    echo ""
    echo "📋 Page ID: $PAGE_ID"
    echo "🔗 URL: https://company.atlassian.net/wiki$PAGE_URL"
    echo ""
    echo "Next steps:"
    echo "  # Add labels:"
    echo "  confluence-cli label add $PAGE_ID --label \"documentation\""
    echo ""
    echo "  # Update content:"
    echo "  confluence-cli page update $PAGE_ID --body \"\$(cat updated.md)\" --format markdown"
else
    echo "❌ Failed to create page"
    echo "$RESULT"
    exit 1
fi
