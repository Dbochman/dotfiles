#!/bin/bash
# Copyright 2026 NVIDIA Corporation
# SPDX-License-Identifier: Apache-2.0

# search-emails.sh - Search emails with smart output handling
# Usage: ./search-emails.sh [options]
#
# Options are passed directly to outlook-cli find.
# This script demonstrates adaptive output format selection.

set -euo pipefail

# Check tools are available
if ! command -v outlook-cli &> /dev/null; then
    echo "Error: outlook-cli not found. See INSTALLATION.md"
    exit 1
fi

if [ $# -eq 0 ]; then
    echo "Usage: $0 <outlook-cli find options>"
    echo "Examples:"
    echo "  $0 --from boss@company.com"
    echo "  $0 --subject 'budget' --after 2025-01-01"
    echo "  $0 --only-unread --has-attachments"
    exit 1
fi

echo "=== Email Search ==="
echo "Filters: $*"
echo

# Step 1: Count results first
echo "Searching..."
COUNT=$(outlook-cli find "$@" --json | jq '.metadata.count')
echo "Found $COUNT emails"
echo

# Step 2: Choose display strategy based on count
if [ "$COUNT" -eq 0 ]; then
    echo "No results. Try broader search criteria."
    exit 0
elif [ "$COUNT" -le 20 ]; then
    echo "Displaying all results (TOON format):"
    outlook-cli find "$@" --toon --fields id,subject,from.emailAddress.address,receivedDateTime
elif [ "$COUNT" -le 100 ]; then
    echo "Displaying first 20 of $COUNT (JSON+jq):"
    outlook-cli find "$@" --json | jq -r '.data[:20] | .[] | "[\(.receivedDateTime | split("T")[0])] \(.from.emailAddress.address): \(.subject)"'
    echo "..."
    echo "(Showing 20 of $COUNT. Add more filters to narrow results.)"
else
    echo "Large result set ($COUNT emails). Summary:"
    echo
    echo "Top senders:"
    outlook-cli find "$@" --json | jq -r '.data[].from.emailAddress.address' | sort | uniq -c | sort -rn | head -5
    echo
    echo "Add more filters (--after, --subject, --from) to narrow results."
fi

echo
echo "=== Search Complete ==="
