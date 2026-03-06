---
name: openclaw-imessage-ack-reaction
description: |
  Configure OpenClaw iMessage reactions/tapbacks. Use when: (1) want OpenClaw agent
  to react to iMessage messages with a tapback (love, like, laugh, etc.),
  (2) ackReaction config in openclaw.json doesn't produce iMessage tapbacks,
  (3) need to understand which reaction types iMessage supports. As of v2026.3.2,
  the agent can send native iMessage tapbacks via the `message` tool with
  `action: "react"`. The `ackReactionScope` config remains a no-op for iMessage
  (only wired for Slack/Discord/Telegram in the gateway).
author: Claude Code
version: 2.0.0
date: 2026-03-06
---

# OpenClaw iMessage Reactions / Tapbacks

## Current Status (v2026.3.2+)

Native iMessage reactions **work** as of v2026.3.2-beta.1. The agent can send tapbacks
via the `message` tool with `action: "react"` and a `message_id` from inbound metadata.

## What Works

- **Agent-initiated reactions**: The agent can react to any inbound message using the
  `message` tool with `action: "react"`. This sends a native iMessage tapback (not a
  text bubble).
- **Supported tapback types**: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`
  (Apple limitation: no custom emoji reactions on iMessage)
- **Requires `message_id`**: The inbound message metadata must include a `message_id`
  for the agent to target. This is provided automatically by the BlueBubbles provider.

## What Does NOT Work

- **`ackReactionScope` config**: The `messages.ackReactionScope` setting in `openclaw.json`
  is a no-op for iMessage. It is only wired for Slack, Discord, and Telegram in the
  gateway code. Setting it has no effect on iMessage channels.
- **`ackReaction` config**: Similarly, `messages.ackReaction` does not trigger automatic
  tapbacks on iMessage. The gateway's ack flow only handles Slack/Discord/Telegram.

## How to Use Reactions

### Option 1: Agent-Driven (via SOUL.md instructions)

Instruct the agent in SOUL.md to react to messages:

```markdown
## Acknowledgment

When you receive an iMessage, react to it with a tapback before responding.
Use the message tool with action "react" and the inbound message_id.
```

The agent will use the `message` tool with `action: "react"` to send a native tapback.

### Option 2: Programmatic (via cron job or skill)

Cron jobs and skills can instruct the agent to react as part of their workflow.

## Verification

1. Send a message to OpenClaw via iMessage
2. Confirm a native tapback appears on the message (not a text bubble)
3. Check gateway logs: look for react-related delivery entries

## Notes

- Tapbacks are native Apple reactions — they appear as the small icon on the message
  bubble, not as a separate text message
- The `imsg react` CLI still exists but is NOT used by the gateway; reactions go through
  the BlueBubbles Private API (`POST /api/v1/message/react`)
- Prior to v2026.3.2, reactions were broken (v2026.2.26) or unsupported (v2026.2.14).
  The old workaround of sending a "eyes" text message is no longer needed.
- Mac Mini SSH user is `dbochman`, not `dylanbochman`
