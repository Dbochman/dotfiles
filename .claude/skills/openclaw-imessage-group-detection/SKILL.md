---
name: openclaw-imessage-group-detection
description: |
  Fix OpenClaw treating iMessage group chat messages as DMs. Use when: (1) OpenClaw agent
  replies to group chat messages as direct messages to the sender instead of the group,
  (2) Gateway logs show "delivered reply to imessage:+1XXXXXXXXXX" for messages that came
  from a group chat, (3) Session lane name shows "imessage:dm:+1XXXXXXXXXX" instead of a
  group session, (4) `imsg send --chat-id N` returns "sent" but message doesn't appear in
  group. Covers the `channels.imessage.groups` config workaround for imsg v0.4.0's missing
  `is_group` field in RPC notifications.
author: Claude Code
version: 2.0.0
date: 2026-02-16
---

# OpenClaw iMessage Group Chat Detection Failure

## Status: Mostly Fixed in imsg v0.5.0

**imsg v0.5.0** (released 2026-02-16) added native group detection:
- `fix: detect groups from ';+;' prefix in guid/identifier for RPC payloads`
- RPC watch notifications now include `is_group` for chats whose guid contains `;+;`

This means OpenClaw's `Boolean(message.is_group)` check now works for most group chats
without needing the `groups` config workaround. The `groups` config is still useful for:
- Overriding per-group settings like `requireMention`
- Edge cases where the guid prefix detection might not work
- Explicit allowlisting when `groupPolicy` is not `"open"`

## Problem (v0.4.0 — now resolved)

OpenClaw's iMessage channel treated all messages from group chats as direct messages (DMs),
causing the agent to reply to the sender individually instead of the group chat. This happened
because `imsg` v0.4.0 did not include an `is_group` field in its RPC watch notifications,
and OpenClaw's fallback group detection required explicit configuration.

## Context / Trigger Conditions

- OpenClaw agent responds to group chat messages but replies go to the sender as a DM
- Gateway log (`~/.openclaw/logs/gateway.log`) shows:
  `[imessage] delivered reply to imessage:+1XXXXXXXXXX` (a phone number, not a chat ID)
- Session lane names look like `session:agent:main:imessage:dm:+1XXXXXXXXXX`
  instead of a group session key

## Root Cause (v0.4.0)

### 1. Missing `is_group` in imsg RPC

`imsg` v0.4.0 sends notifications via JSON-RPC when watching for messages,
but did NOT include `is_group: true/false` in the payload.

### 2. OpenClaw's Group Detection Logic

In `loader-n6BPnYom.js`, OpenClaw determines if a message is from a group:

```javascript
const isGroup = Boolean(message.is_group) || treatAsGroupByConfig;
```

Since `message.is_group` was always `undefined` in v0.4.0, `isGroup` depended entirely on
the `groups` config workaround.

## Solution

### Preferred: Upgrade imsg to v0.5.0+

```bash
brew upgrade imsg
# Then restart OpenClaw gateway to pick up the new binary
```

The gateway spawns `imsg rpc` as a child process — it must be restarted to use the new version.
Check with: `ps aux | grep imsg` — verify the cellar path shows `0.5.0`.

### Fallback: Groups Config Workaround (still works)

If group detection still fails for specific chats, add them to the `groups` config
in `~/.openclaw/openclaw.json`:

```json
{
  "channels": {
    "imessage": {
      "groups": {
        "CHAT_ROWID": {
          "requireMention": false
        },
        "*": {}
      }
    }
  }
}
```

### Finding the Chat ID

```bash
sqlite3 ~/Library/Messages/chat.db \
  "SELECT ROWID, chat_identifier, display_name, style FROM chat WHERE style = 43;"
```

- `style = 43` = group chat, `style = 45` = DM
- Use the `ROWID` value as the key in the `groups` config

### Config Hot Reload

OpenClaw detects config changes and hot-reloads — no manual restart needed after editing config.

## New Features in imsg v0.5.0

- **Reactions**: `imsg react` command + reaction events in `watch` stream
- **Typing indicators**: `imsg typing` command + RPC methods
- **`--reactions` flag**: For `watch` command to include tapback events
- **`include_reactions` toggle**: In `watch.subscribe` RPC
- **`thread_originator_guid`**: In message output (for threaded replies)
- **`destination_caller_id`**: In message output

## Related Issues

### `imsg send --chat-id N` Hangs from SSH

AppleScript automation requires macOS TCC permission. `sshd` doesn't have it,
so `osascript` hangs forever. The OpenClaw gateway works because `OpenClawGateway.app`
is a proper macOS app bundle with its own TCC entry.

**Do not** try to debug group chat sending via SSH — it will always hang.

### imsg `--to` vs `--chat-id` vs `--chat-guid`

| Flag | AppleScript Path | Use For |
|------|-----------------|---------|
| `--to` | `buddy theRecipient` | Individual DMs (phone/email) |
| `--chat-id N` | `chat id "guid"` (looks up guid from ROWID) | Group chats by ROWID |
| `--chat-guid "..."` | `chat id "guid"` | Group chats by full guid |
