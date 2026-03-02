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

   When you receive a message, your FIRST action — before thinking, processing,
   or drafting anything — must be to send a message containing only "👀"
   (nothing else, no other text). This is a read receipt so the sender knows
   you saw it.

   Then, AFTER that message is sent, begin working on your actual response and
   send it as a second, separate message.

   Critical: "👀" must be its own standalone message. Never combine it with your
   actual reply. Never append text after it. Two separate messages every time.
   ```

   **Important**: The initial simpler instruction ("send 👀 first, then follow up")
   resulted in the agent combining the 👀 with its reply in a single message.
   The stronger, more explicit instruction above is needed to force two separate
   messages.

3. Changes to SOUL.md take effect immediately — no session reset needed. SOUL.md
   is loaded fresh as a context file on each inbound message.

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
