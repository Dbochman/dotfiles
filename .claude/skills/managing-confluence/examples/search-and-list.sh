#!/bin/bash
# search-and-list.sh - Search Confluence and display formatted results
#
# Usage: ./search-and-list.sh <query> [--space SPACE] [--limit N]
# Example: ./search-and-list.sh "API documentation"
#          ./search-and-list.sh "deployment" --space ENG
#          ./search-and-list.sh "guide" --space DOCS --limit 20

set -e

# Parse arguments
QUERY=""
SPACE=""
LIMIT="25"

while [[ $# -gt 0 ]]; do
    case $1 in
        --space)
            SPACE="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        *)
            QUERY="$1"
            shift
            ;;
    esac
done

if [ -z "$QUERY" ]; then
    echo "Usage: $0 <query> [--space SPACE] [--limit N]"
    echo ""
    echo "Options:"
    echo "  --space SPACE  Search only in specific space"
    echo "  --limit N      Maximum results (default: 25)"
    echo ""
    echo "Examples:"
    echo "  $0 \"API documentation\""
    echo "  $0 \"deployment\" --space ENG"
    echo "  $0 \"guide\" --space DOCS --limit 50"
    exit 1
fi

echo "Searching Confluence..."
echo "════════════════════════════════════════"
echo "🔍 Query: $QUERY"
[ -n "$SPACE" ] && echo "📁 Space: $SPACE"
echo "📊 Limit: $LIMIT"
echo ""

# Build search command
SEARCH_CMD="confluence-cli search \"$QUERY\" --limit $LIMIT"
[ -n "$SPACE" ] && SEARCH_CMD="$SEARCH_CMD --space \"$SPACE\""
SEARCH_CMD="$SEARCH_CMD --json"

# Execute search
RESULTS=$(eval "$SEARCH_CMD")

TOTAL=$(echo "$RESULTS" | jq '.totalSize')
COUNT=$(echo "$RESULTS" | jq '.results | length')

echo "Found $TOTAL total results (showing $COUNT)"
echo ""
echo "Results:"
echo "────────────────────────────────────────"

# Display results
echo "$RESULTS" | jq -r '.results[] | "\(.id)|\(.title)|\(.space.key)|\(.excerpt // "No excerpt")"' | \
    while IFS='|' read -r id title space excerpt; do
        # Truncate excerpt
        excerpt=$(echo "$excerpt" | sed 's/<[^>]*>//g' | head -c 100)
        
        echo ""
        echo "📄 $title"
        echo "   Space: $space | ID: $id"
        echo "   $excerpt..."
    done

echo ""
echo "────────────────────────────────────────"
echo ""
echo "Commands for working with results:"
echo ""
echo "  # Read a page:"
echo "  confluence-cli page get <id> --format markdown"
echo ""
echo "  # Export all results:"
echo "  $0 \"$QUERY\" $([ -n "$SPACE" ] && echo "--space $SPACE") | grep 'ID:' | awk '{print \$NF}' | while read id; do"
echo "    confluence-cli page get \"\$id\" --format markdown > \"\$id.md\""
echo "  done"
