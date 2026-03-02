#!/bin/bash
# team-export.sh - Export team data to CSV
#
# Usage: ./team-export.sh <department|manager> <value> [output-file]
# Example: ./team-export.sh department "Platform Engineering" team.csv
#          ./team-export.sh manager msmith team.csv

set -e

if [ -z "$2" ]; then
    echo "Usage: $0 <filter-type> <filter-value> [output-file]"
    echo ""
    echo "Filter types:"
    echo "  department  - Filter by department name"
    echo "  manager     - Filter by manager login"
    echo "  site        - Filter by site/location"
    echo "  country     - Filter by country code"
    echo ""
    echo "Examples:"
    echo "  $0 department 'Platform Engineering' team.csv"
    echo "  $0 manager msmith team.csv"
    echo "  $0 site 'Santa Clara' sc_team.csv"
    echo "  $0 country US us_employees.csv"
    exit 1
fi

FILTER_TYPE="$1"
FILTER_VALUE="$2"
OUTPUT_FILE="${3:-team_export.csv}"

echo "Exporting team data..."
echo "Filter: $FILTER_TYPE = $FILTER_VALUE"
echo "Output: $OUTPUT_FILE"
echo "════════════════════════════════════════"

# Build the jq filter based on type
case "$FILTER_TYPE" in
    department)
        JQ_FILTER=".[] | select(.attributes.department == \"$FILTER_VALUE\")"
        ;;
    manager)
        JQ_FILTER=".[] | select(.attributes.manager == \"$FILTER_VALUE\")"
        ;;
    site)
        JQ_FILTER=".[] | select(.attributes.site == \"$FILTER_VALUE\")"
        ;;
    country)
        JQ_FILTER=".[] | select(.attributes.country == \"$FILTER_VALUE\")"
        ;;
    *)
        echo "❌ Unknown filter type: $FILTER_TYPE"
        echo "Valid types: department, manager, site, country"
        exit 1
        ;;
esac

# Fetch data
echo "Fetching data..."
DATA=$(helios-cli user search --filter-active true --page-size 1000)

# Count results
COUNT=$(echo "$DATA" | jq "[$JQ_FILTER] | length")
echo "Found $COUNT employees"

if [ "$COUNT" = "0" ]; then
    echo "❌ No employees found matching criteria"
    exit 1
fi

# Export to CSV
echo "Exporting to CSV..."
echo "$DATA" | jq -r "
    [\"Name\",\"Email\",\"Login\",\"Title\",\"Department\",\"Manager\",\"Site\",\"Country\",\"Hire Date\",\"Employment Type\",\"Is Manager\",\"Direct Reports\"],
    ([$JQ_FILTER] | sort_by(.attributes.cn) | .[] | [
        .attributes.cn,
        .attributes.email,
        .attributes.login,
        .attributes.title,
        .attributes.department,
        .attributes.manager,
        .attributes.site,
        .attributes.country,
        .attributes.hireDate,
        .attributes.employmentType,
        .attributes.isManager,
        .attributes.childUserCount
    ]) | @csv" > "$OUTPUT_FILE"

echo "════════════════════════════════════════"
echo "✅ Export complete!"
echo ""
echo "File: $OUTPUT_FILE"
echo "Rows: $COUNT (plus header)"
echo ""
echo "Preview (first 5 rows):"
head -6 "$OUTPUT_FILE" | column -t -s','
echo ""

# Generate summary statistics
echo "Summary Statistics:"
echo "────────────────────────────────────────"

echo "$DATA" | jq "[$JQ_FILTER] | {
    total: length,
    by_type: (group_by(.attributes.employmentType) | map({type: .[0].attributes.employmentType, count: length})),
    managers: ([.[] | select(.attributes.isManager == true)] | length),
    ics: ([.[] | select(.attributes.isManager == false)] | length),
    by_country: (group_by(.attributes.country) | map({country: .[0].attributes.country, count: length}) | sort_by(.count) | reverse | .[:5])
}" | jq -r '
"Total Employees: \(.total)
Managers: \(.managers) (\(.managers * 100 / .total | floor)%)
Individual Contributors: \(.ics)

By Employment Type:
\(.by_type | map("  \(.type): \(.count)") | join("\n"))

Top Countries:
\(.by_country | map("  \(.country): \(.count)") | join("\n"))
"'
