---
name: openclaw-stale-session-and-identity-mismatch
description: |
  Fix OpenClaw agent repeatedly claiming a Google account "isn't authenticated" or failing
  with auth errors even though GWS tokens are valid. Use when: (1) GWS CLI works
  manually (`gws gmail users messages list --account X`) but OpenClaw agent says auth is missing,
  (2) Agent uses wrong email format (e.g., missing dots in Gmail address), (3) Agent
  keeps repeating stale beliefs about auth status despite fixes. Covers identity mismatch
  across config files and stale session cache clearing.
author: Claude Code
version: 2.0.0
date: 2026-03-05
---

# OpenClaw Stale Session & Identity Mismatch

## Problem
OpenClaw agent fails to use Google services (Gmail, Calendar, Drive) for a specific account,
claiming authentication is missing. The actual GWS CLI works fine when tested manually with
the correct `--account` flag. The root cause is typically a **wrong email format** in
OpenClaw's identity/config files combined with **stale session history** that preserves
the agent's incorrect belief.

## Context / Trigger Conditions

- Agent replies "I need to authenticate X first" but GWS credentials exist
- Agent uses wrong email (e.g., `userwithoutdots@gmail.com` instead of `user@gmail.com`)
- Manual test works: `gws gmail users messages list --params '{"userId":"me","q":"is:unread","maxResults":1}' --account user@gmail.com`
- Problem persists even after verifying GWS credentials are valid

## Root Causes

### 1. Email Format Mismatch (Primary)
GWS does **strict email matching** against its encrypted credentials. Gmail treats
`userwithoutdots@gmail.com` and `user@gmail.com` as the same mailbox, but
GWS stores credentials under the **exact email used during OAuth**. If any OpenClaw config
file has the wrong version, the agent will pass the wrong email to `--account`.

### 2. Multiple Sources of Wrong Email
The wrong email can propagate across multiple files:

| File | What it affects |
|------|-----------------|
| `~/.openclaw/workspace/USER.md` | Agent's identity context for contacts |
| `~/.openclaw/workspace/memory/*.md` | Accumulated knowledge from past sessions |
| `~/.openclaw/cron/jobs.json` | Scheduled tasks that use the email |
| `~/.openclaw/skills/gws-gmail/SKILL.md` | Skill file with account examples |
| `~/.openclaw/skills/gws-calendar/SKILL.md` | Skill file with account examples |

### 3. Stale Session History
OpenClaw sessions are persistent JSONL files. Once the agent encounters an auth error, that
failure becomes part of the conversation history. Even after fixing the email, the agent's
compacted session context retains the "not authenticated" belief.

## Solution

### Step 1: Verify GWS credentials are valid
```bash
ssh dylans-mac-mini 'gws gmail users messages list --params "{\"userId\":\"me\",\"q\":\"is:unread\",\"maxResults\":1}" --account user@gmail.com'
```

### Step 2: Check which accounts GWS knows about
```bash
ssh dylans-mac-mini 'gws auth list'
```
If this shows empty, test with an actual API call — the JS wrapper may use a stale cached binary.

### Step 3: Fix email in all config files
```bash
# Find all files with the wrong email (exclude session .jsonl and .log files)
ssh dylans-mac-mini 'grep -rl "juliajoyjennings" ~/.openclaw/ 2>/dev/null | grep -v ".jsonl" | grep -v ".log"'

# Fix each file
ssh dylans-mac-mini 'sed -i "" "s/userwithoutdots@gmail.com/user@gmail.com/g" <files>'
```

### Step 4: Clear the stale session
Find the session ID for the affected contact:
```bash
ssh dylans-mac-mini "python3 -c '
import json
with open(\"/Users/dbochman/.openclaw/agents/main/sessions/sessions.json\") as f:
    data = json.load(f)
for key, val in data.items():
    if \"+1XXXXXXXXXX\" in key:  # Replace with contact phone number
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
key = \"agent:main:imessage:dm:+1XXXXXXXXXX\"
if key in data:
    del data[key]
    with open(path, \"w\") as f:
        json.dump(data, f, indent=2)
    print(\"Removed\", key)
'"
```

### Step 5: Verify the fix
Have the contact send a new message. The agent will create a fresh session and read the
corrected email from the skill files.

## Verification

1. Check logs for the new session — should see `exec` tool calls with correct email
2. No more auth errors
3. Agent successfully reads/sends Gmail, creates calendar events

## Notes

- Gmail ignores dots in the local part, but GWS does NOT. Always use the exact email
  that was used during `gws auth login`.
- GWS credentials are AES-256-GCM encrypted at `~/.config/gws/` — per-account files
  named `credentials.<base64>.enc`. The `.encryption_key` file must also be present.
- If GWS auth is broken, re-auth locally (requires browser) then scp credentials +
  `.encryption_key` + `accounts.json` to Mini.
- **DANGER: `gws auth logout` without `--account <email>` nukes ALL accounts.**
- Session files can be very large (4MB+) after many compactions. Clearing them is safe
  — the agent just loses conversation history for that contact.
