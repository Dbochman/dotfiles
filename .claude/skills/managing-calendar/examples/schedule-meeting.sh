#!/bin/bash
# Copyright 2026 NVIDIA Corporation
# SPDX-License-Identifier: Apache-2.0

# schedule-meeting.sh - Schedule a meeting with conflict check
# Usage: ./schedule-meeting.sh "Subject" "2025-12-20T14:00:00" "2025-12-20T15:00:00" "attendee@example.com"
#
# This script demonstrates a meeting scheduling workflow:
# 1. Check for conflicts
# 2. Create the meeting if no conflicts
# 3. Display confirmation

set -euo pipefail

# Check tools are available
if ! command -v calendar-cli &> /dev/null; then
    echo "Error: calendar-cli not found. See INSTALLATION.md"
    exit 1
fi

# Parse arguments
if [ $# -lt 3 ]; then
    echo "Usage: $0 <subject> <start> <end> [attendees]"
    echo "Example: $0 'Team Sync' '2025-12-20T14:00:00' '2025-12-20T15:00:00' 'alice@example.com,bob@example.com'"
    exit 1
fi

SUBJECT="$1"
START="$2"
END="$3"
ATTENDEES="${4:-}"

# Extract date for conflict check
START_DATE="${START%%T*}"
END_DATE="${END%%T*}"

echo "=== Schedule Meeting ==="
echo "Subject: $SUBJECT"
echo "Start: $START"
echo "End: $END"
[ -n "$ATTENDEES" ] && echo "Attendees: $ATTENDEES"
echo

# Step 1: Check for conflicts
echo "Checking for conflicts..."
CONFLICTS=$(calendar-cli find --after "$START_DATE" --before "$END_DATE" --json | jq -r --arg start "$START" --arg end "$END" '
    .data[] | 
    select(
        (.start.dateTime < $end) and (.end.dateTime > $start)
    ) | 
    "\(.start.dateTime) - \(.end.dateTime): \(.subject)"
')

if [ -n "$CONFLICTS" ]; then
    echo "⚠️  Potential conflicts found:"
    echo "$CONFLICTS"
    echo
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
else
    echo "✓ No conflicts found"
fi
echo

# Step 2: Create the meeting
echo "Creating meeting..."
if [ -n "$ATTENDEES" ]; then
    calendar-cli create \
        --subject "$SUBJECT" \
        --start "$START" \
        --end "$END" \
        --attendees "$ATTENDEES" \
        --toon
else
    calendar-cli create \
        --subject "$SUBJECT" \
        --start "$START" \
        --end "$END" \
        --toon
fi

echo
echo "=== Meeting Scheduled ==="
