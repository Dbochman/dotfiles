#!/bin/bash
# Copyright 2026 NVIDIA Corporation
# SPDX-License-Identifier: Apache-2.0

# triage-inbox.sh - Quick inbox triage workflow
# Usage: ./triage-inbox.sh [--archive-newsletters]
#
# This script demonstrates a basic inbox triage workflow:
# 1. Check unread count
# 2. Display recent unread emails
# 3. Optionally archive newsletters

set -euo pipefail

# Check tools are available
if ! command -v outlook-cli &> /dev/null; then
    echo "Error: outlook-cli not found. See INSTALLATION.md"
    exit 1
fi

echo "=== Inbox Triage ==="
echo

# Step 1: Check unread count
echo "Checking unread emails..."
COUNT=$(outlook-cli find --only-unread --json | jq '.metadata.count')
echo "Found $COUNT unread emails"
echo

# Step 2: Choose format based on count
if [ "$COUNT" -eq 0 ]; then
    echo "✓ Inbox is clean!"
    exit 0
elif [ "$COUNT" -le 50 ]; then
    echo "Recent unread emails:"
    outlook-cli find --only-unread --toon --fields id,subject,from.emailAddress.address,receivedDateTime
else
    echo "Large inbox ($COUNT unread). Showing first 20:"
    outlook-cli find --only-unread --json | jq -r '.data[:20] | .[] | "[\(.receivedDateTime)] \(.from.emailAddress.address): \(.subject)"'
fi
echo

# Step 3: Optional newsletter archiving
if [ "${1:-}" = "--archive-newsletters" ]; then
    echo "Archiving newsletters..."
    # Preview first
    NEWSLETTER_COUNT=$(outlook-cli find --only-unread --subject "newsletter" --json | jq '.metadata.count')
    if [ "$NEWSLETTER_COUNT" -gt 0 ]; then
        echo "Found $NEWSLETTER_COUNT newsletters. Archiving..."
        outlook-cli move --all --only-unread --subject "newsletter" Archive --toon
        echo "✓ Archived $NEWSLETTER_COUNT newsletters"
    else
        echo "No newsletters to archive"
    fi
fi

echo
echo "=== Triage Complete ==="
