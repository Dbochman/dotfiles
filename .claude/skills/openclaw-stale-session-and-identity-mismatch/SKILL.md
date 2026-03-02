---
name: openclaw-stale-session-and-identity-mismatch
description: |
  Fix OpenClaw agent repeatedly claiming a Google account "isn't authenticated" or failing
  with "No auth for calendar" even though GOG tokens are valid. Use when: (1) GOG CLI works
  manually (`gog calendar events --account=X`) but OpenClaw agent says auth is missing,
  (2) Agent uses wrong email format (e.g., missing dots in Gmail address), (3) Error log
  shows "No auth for calendar juliajoyjennings@gmail.com" with a dotless email, (4) Agent
  keeps repeating stale beliefs about auth status despite fixes. Covers identity mismatch
  across USER.md/cron/memory files and stale session cache clearing.
author: Claude Code
version: 1.0.0
date: 2026-02-09
---

# OpenClaw Stale Session & Identity Mismatch

## Problem
OpenClaw agent fails to use Google Calendar (or other GOG services) for a specific account,
claiming authentication is missing. The actual GOG CLI works fine when tested manually with
the correct `--account=` flag. The root cause is typically a **wrong email format** in
OpenClaw's identity/config files combined with **stale session history** that preserves
the agent's incorrect belief.

## Context / Trigger Conditions

- Agent replies "I need to authenticate X's calendar first" but tokens exist in GOG keyring
- OpenClaw error log shows: `No auth for calendar juliajoyjennings@gmail.com` (dotless email)
- GOG CLI error: `missing --account` (agent omits the flag entirely)
- Manual test works: `gog calendar events --today --account=julia.joy.jennings@gmail.com`
- Problem persists even after verifying GOG tokens are valid and refreshed

## Root Causes

### 1. Email Format Mismatch (Primary)
GOG CLI does **strict email matching** against its encrypted keyring tokens. Gmail treats
`juliajoyjennings@gmail.com` and `julia.joy.jennings@gmail.com` as the same mailbox, but
GOG stores the token under the **exact email returned by Google OAuth** (with dots). If any
OpenClaw config file has the dotless version, the agent will pass the wrong email to `--account=`.

### 2. Multiple Sources of Wrong Email
The wrong email can propagate across multiple files that the agent reads:

| File | What it affects |
|------|-----------------|
| `~/.openclaw/workspace/USER.md` | Agent's identity context for contacts |
| `~/.openclaw/workspace/memory/*.md` | Accumulated knowledge from past sessions |
| `~/.openclaw/cron/jobs.json` | Scheduled tasks that use the email |
| `~/.openclaw/skills/calendar/SKILL.md` | Skill file with account examples |

### 3. Stale Session History
OpenClaw sessions are persistent JSONL files. Once the agent encounters an auth error, that
failure becomes part of the conversation history. Even after fixing the email, the agent's
compacted session context retains the "not authenticated" belief and may continue to claim
auth is missing.

## Solution

### Step 1: Verify GOG tokens are valid
```bash
ssh dylans-mac-mini 'export GOG_KEYRING_PASSWORD=$(cat ~/.cache/openclaw-gateway/gog_keyring_password) && /opt/homebrew/bin/gog calendar events --today --account=julia.joy.jennings@gmail.com'
```

### Step 2: Check error logs for wrong email format
```bash
ssh dylans-mac-mini 'grep -E "No auth for|missing --account" /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log'
```
Look for dotless email or missing `--account` flag.

### Step 3: Fix email in all config files
```bash
# Find all files with the wrong email (exclude session .jsonl and .log files)
ssh dylans-mac-mini 'grep -rl "juliajoyjennings" ~/.openclaw/ 2>/dev/null | grep -v ".jsonl" | grep -v ".log"'

# Fix each file
ssh dylans-mac-mini 'sed -i "" "s/juliajoyjennings@gmail.com/julia.joy.jennings@gmail.com/g" ~/.openclaw/workspace/USER.md ~/.openclaw/workspace/memory/*.md ~/.openclaw/cron/jobs.json ~/.openclaw/cron/jobs.json.bak'
```

### Step 4: Clear the stale session
Find the session ID for the affected contact:
```bash
ssh dylans-mac-mini "python3 -c '
import json
with open(\"/Users/dbochman/.openclaw/agents/main/sessions/sessions.json\") as f:
    data = json.load(f)
for key, val in data.items():
    if \"+15084234853\" in key:  # Replace with contact phone number
        print(\"Key:\", key)
        print(\"SessionId:\", val.get(\"sessionId\"))
        print(\"File:\", val.get(\"sessionFile\"))
'"
```

Delete the session file and remove from sessions.json:
```bash
# Delete session file
ssh dylans-mac-mini 'rm ~/.openclaw/agents/main/sessions/<sessionId>.jsonl'

# Remove from sessions.json
ssh dylans-mac-mini "python3 -c '
import json
path = \"/Users/dbochman/.openclaw/agents/main/sessions/sessions.json\"
with open(path) as f:
    data = json.load(f)
key = \"agent:main:imessage:dm:+15084234853\"
if key in data:
    del data[key]
    with open(path, \"w\") as f:
        json.dump(data, f, indent=2)
    print(\"Removed\", key)
'"
```

### Step 5: Verify the fix
Have the contact send a new message. The agent will create a fresh session and read the
corrected email from USER.md and the skill file.

## Verification

1. Check logs for the new session — should see `exec` tool calls with correct email
2. No more `No auth for calendar` errors
3. Agent successfully creates/reads calendar events

## Notes

- Gmail ignores dots in the local part, but GOG does NOT. Always use the exact email
  that Google OAuth returns (check the keyring file names in
  `~/Library/Application Support/gogcli/keyring/`).
- The agent may still omit `--account` on its first try in a new session (Haiku model
  behavior), but self-corrects on retry after reading the error message.
- Session files can be very large (4MB+) after many compactions. Clearing them is safe
  — the agent just loses conversation history for that contact.
- Always do a final sweep: `grep -rl "juliajoyjennings" ~/.openclaw/ | grep -v .jsonl | grep -v .log`
  to catch all instances.
