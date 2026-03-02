#!/bin/bash
# lookup-user.sh - Look up an NVIDIA employee by email or login
# 
# Usage: ./lookup-user.sh <email-or-login>
# Example: ./lookup-user.sh jdoe@nvidia.com
#          ./lookup-user.sh jdoe

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <email-or-login>"
    echo "Example: $0 jdoe@nvidia.com"
    exit 1
fi

USER_ID="$1"

echo "Looking up user: $USER_ID"
echo "════════════════════════════════════════"

# Get user profile
PROFILE=$(helios-cli user get "$USER_ID")

if [ -z "$PROFILE" ] || echo "$PROFILE" | grep -q "not_found"; then
    echo "❌ User not found: $USER_ID"
    echo ""
    echo "Try searching by name:"
    echo "  helios-cli user search --filter-active true | jq '.[] | select(.attributes.cn | test(\"NAME\"; \"i\"))'"
    exit 1
fi

# Extract and display key fields
echo "$PROFILE" | jq -r '
"
📧 Email:       \(.attributes.email)
👤 Login:       \(.attributes.login)
📛 Name:        \(.attributes.cn)
💼 Title:       \(.attributes.title)
🏢 Department:  \(.attributes.department)
👔 Manager:     \(.attributes.manager)
📍 Site:        \(.attributes.site)
🌍 Country:     \(.attributes.country)
📅 Hire Date:   \(.attributes.hireDate)
👥 Type:        \(.attributes.employmentType)
📊 Is Manager:  \(.attributes.isManager)
👥 Direct Rpts: \(.attributes.childUserCount)
"'

echo ""
echo "Manager Chain:"
echo "$PROFILE" | jq -r '.attributes.managerChain | to_entries | .[] | "  \(.key + 1). \(.value.name) (\(.value.login))"'

echo ""
echo "════════════════════════════════════════"
echo "Additional commands:"
echo "  # View team members (peers):"
MANAGER=$(echo "$PROFILE" | jq -r '.attributes.manager')
echo "  helios-cli user search --filter-active true --page-size 200 | jq '[.[] | select(.attributes.manager == \"$MANAGER\")]'"
echo ""
echo "  # Check group memberships:"
echo "  helios-cli relationship user-groups --page-size 100 | jq '.[] | select(.attributes.userDn | test(\"NAME\"; \"i\"))'"
echo ""
echo "  # Download photo:"
echo "  helios-cli user get $USER_ID | jq -r '.attributes.base64Photo' | base64 -d > photo.jpg"
