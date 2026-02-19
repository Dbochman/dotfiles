---
name: openclaw-imessage-ack-reaction
description: |
  Fix OpenClaw ackReaction not working for iMessage channel. Use when:
  (1) messages.ackReaction is configured in openclaw.json but no tapback/reaction
  appears on iMessage messages, (2) ackReaction works for Slack/Discord/Telegram
  but not iMessage, (3) want the OpenClaw agent to acknowledge iMessage receipt
  with an emoji. The ackReaction feature is only wired to Slack, Discord, and
  Telegram in the gateway code — iMessage is not supported as of v2026.2.14.
  Workaround: add acknowledgment instructions to SOUL.md.
author: Claude Code
version: 1.0.0
date: 2026-02-19
---

# OpenClaw iMessage Ack Reaction Not Supported

## Problem

Setting `messages.ackReaction` and `messages.ackReactionScope` in `openclaw.json`
has no effect on the iMessage channel. The gateway accepts the config and even
hot-reloads it, but never attempts to send a tapback reaction via the `imsg react`
CLI command.

## Context / Trigger Conditions

- `openclaw.json` has `messages.ackReaction` set (e.g., `"👀"`)
- Gateway logs show `config change applied (dynamic reads: messages.ackReaction...)`
- But no reaction/tapback ever appears on inbound iMessage messages
- The `imsg react` CLI command exists and works manually
- Works fine for Slack, Discord, and Telegram channels

## Root Cause

In the gateway source (`pi-embedded-8DITBEle.js`), the `ackReactionPromise` logic
is only implemented for three channels:

- **Discord**: `shouldAckReaction$3()` → `reactMessageDiscord()`
- **Slack**: `shouldAckReaction$2()` → `reactSlackMessage()`
- **Telegram**: `shouldAckReaction$1()` → Telegram reaction API

The iMessage monitor (`monitorIMessageProvider` / `deliverReplies$3`) has no
`ackReactionPromise` handling whatsoever. The `imsg react` CLI exists but is not
wired into the gateway's ack flow.

## Solution (Workaround)

Since native ackReaction isn't supported for iMessage, instruct the agent to send
a text-based acknowledgment via SOUL.md:

1. Remove the useless config entries from `openclaw.json`:
   ```python
   config['messages'].pop('ackReaction', None)
   config['messages'].pop('ackReactionScope', None)
   ```

2. Add an acknowledgment section to `~/.openclaw/workspace/SOUL.md`:
   ```markdown
   ## Acknowledgment

   When you receive a message, immediately reply with just "👀" before you start
   working on your response. This lets the sender know you've seen their message
   and are on it. Send the 👀 as a separate message first, then follow up with
   your actual reply.
   ```

3. The agent will pick this up on the next session reset or `/reset`.

## Trade-offs

- **Text vs tapback**: The 👀 shows as a message bubble, not a native iMessage
  tapback/reaction on the original message
- **Extra message**: Creates an additional message in the conversation
- **LLM-dependent**: The agent may occasionally skip it depending on context

## Verification

1. Send a message to the OpenClaw agent via iMessage
2. Confirm you receive a "👀" text reply before the actual response
3. Check gateway logs for the delivery: `imessage: delivered reply to chat_id:XXX`

## Key Files (Mac Mini)

- Config: `/Users/dbochman/.openclaw/openclaw.json`
- Soul: `/Users/dbochman/.openclaw/workspace/SOUL.md`
- Gateway logs: `/tmp/openclaw/openclaw-YYYY-MM-DD.log`
- imsg CLI: `/opt/homebrew/bin/imsg`
- Gateway source: `/opt/homebrew/lib/node_modules/openclaw/dist/pi-embedded-8DITBEle.js`

## Notes

- The `imsg react` command uses UI automation (System Events/accessibility) and can
  only react to the most recent incoming message — this may be why OpenClaw hasn't
  wired it up natively (fragile compared to Slack/Discord/Telegram APIs)
- Mac Mini SSH user is `dbochman`, not `dylanbochman`
- Gateway process runs as `openclaw-gateway` launched from
  `/Users/dbochman/Applications/OpenClawGateway.app`
- OpenClaw version as of this writing: `2026.2.14`
