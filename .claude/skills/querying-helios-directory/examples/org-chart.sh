#!/bin/bash
# org-chart.sh - Generate an org chart for a manager
#
# Usage: ./org-chart.sh <manager-login> [depth]
# Example: ./org-chart.sh msmith
#          ./org-chart.sh msmith 2

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <manager-login> [depth]"
    echo "Example: $0 msmith      # Direct reports only"
    echo "         $0 msmith 2    # Include sub-teams"
    exit 1
fi

MANAGER="$1"
DEPTH="${2:-1}"

echo "Org Chart for: $MANAGER"
echo "Depth: $DEPTH level(s)"
echo "════════════════════════════════════════"
echo ""

# Get manager's info first
MANAGER_INFO=$(helios-cli user get "$MANAGER" 2>/dev/null || echo "{}")
MANAGER_NAME=$(echo "$MANAGER_INFO" | jq -r '.attributes.cn // "Unknown"')
MANAGER_TITLE=$(echo "$MANAGER_INFO" | jq -r '.attributes.title // "Unknown"')

echo "📊 $MANAGER_NAME"
echo "   $MANAGER_TITLE"
echo "   ────────────────────────────────────"

# Function to print team at a given indent level
print_team() {
    local mgr=$1
    local indent=$2
    local current_depth=$3
    local max_depth=$4
    
    # Get direct reports
    local reports
    reports=$(helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$mgr" '
        [.[] | select(.attributes.manager == $mgr)] | sort_by(.attributes.cn)')
    
    local count
    count=$(echo "$reports" | jq 'length')
    
    if [ "$count" = "0" ]; then
        return
    fi
    
    echo "$reports" | jq -r '.[] | "\(.attributes.login)|\(.attributes.cn)|\(.attributes.title)|\(.attributes.isManager)|\(.attributes.childUserCount)"' | while IFS='|' read -r login name title is_mgr direct_count; do
        if [ "$is_mgr" = "true" ]; then
            echo "${indent}├── 👔 $name ($login)"
            echo "${indent}│      $title - $direct_count direct reports"
            
            # Recurse if we haven't hit max depth
            if [ "$current_depth" -lt "$max_depth" ]; then
                print_team "$login" "${indent}│   " "$((current_depth + 1))" "$max_depth"
            fi
        else
            echo "${indent}├── 👤 $name ($login)"
            echo "${indent}│      $title"
        fi
    done
}

# Print the org tree
print_team "$MANAGER" "   " 1 "$DEPTH"

echo ""
echo "════════════════════════════════════════"

# Summary stats
TOTAL=$(helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" '
    [.[] | select(.attributes.manager == $mgr)] | length')

echo ""
echo "Summary:"
echo "  Direct reports: $TOTAL"

# Count managers in directs
SUBMGRS=$(helios-cli user search --filter-active true --page-size 500 | jq --arg mgr "$MANAGER" '
    [.[] | select(.attributes.manager == $mgr and .attributes.isManager == true)] | length')
echo "  Sub-managers: $SUBMGRS"

echo ""
echo "Export options:"
echo "  # Export to CSV:"
echo "  helios-cli user search --filter-active true --page-size 500 | jq --arg mgr \"$MANAGER\" -r '"
echo "    [\"Name\",\"Email\",\"Title\",\"Is Manager\"],"
echo "    (.[] | select(.attributes.manager == \$mgr) | [.attributes.cn,.attributes.email,.attributes.title,.attributes.isManager]) | @csv'"
