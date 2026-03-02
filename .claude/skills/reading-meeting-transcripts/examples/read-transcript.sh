#!/bin/bash
# Copyright 2026 NVIDIA Corporation
# SPDX-License-Identifier: Apache-2.0

# read-transcript.sh - Read meeting transcript from calendar event
# Usage: ./read-transcript.sh <event-id>
#        ./read-transcript.sh --find "Meeting Subject" [--after YYYY-MM-DD]
#
# This script demonstrates the transcript workflow:
# 1. Find meeting (if searching by subject)
# 2. Read transcript
# 3. Display with speaker labels

set -euo pipefail

# Check tools are available
if ! command -v transcript-cli &> /dev/null || ! command -v calendar-cli &> /dev/null; then
    echo "Error: transcript-cli and calendar-cli required. See INSTALLATION.md"
    exit 1
fi

echo "=== Read Meeting Transcript ==="
echo

# Determine mode
if [ "${1:-}" = "--find" ]; then
    # Search mode: find event by subject
    SUBJECT="${2:?Subject required}"
    AFTER="${4:-$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d '30 days ago' +%Y-%m-%d)}"
    
    echo "Searching for meeting: $SUBJECT (since $AFTER)"
    
    # Find matching events
    EVENTS=$(calendar-cli find --after "$AFTER" --subject "$SUBJECT" --json)
    COUNT=$(echo "$EVENTS" | jq '.metadata.count')
    
    if [ "$COUNT" -eq 0 ]; then
        echo "No meetings found matching '$SUBJECT'"
        exit 1
    elif [ "$COUNT" -eq 1 ]; then
        EVENT_ID=$(echo "$EVENTS" | jq -r '.data[0].id')
        echo "Found: $(echo "$EVENTS" | jq -r '.data[0].subject')"
    else
        echo "Found $COUNT meetings. Select one:"
        echo "$EVENTS" | jq -r '.data[] | "\(.start.dateTime): \(.subject)"' | head -10 | nl
        read -p "Enter number [1]: " -r NUM
        NUM="${NUM:-1}"
        EVENT_ID=$(echo "$EVENTS" | jq -r ".data[$((NUM-1))].id")
    fi
else
    # Direct mode: use provided event ID
    EVENT_ID="${1:?Event ID required. Use --find to search by subject.}"
fi

echo
echo "Reading transcript for event: $EVENT_ID"
echo

# Read transcript
transcript-cli read --event-id "$EVENT_ID" --toon

echo
echo "=== Transcript Complete ==="
