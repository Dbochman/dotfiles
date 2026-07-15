---
name: "reachy-continuity"
description: "Handle Reachy/iMessage handoffs, selective durable memory, forgetting, and diagnostics; automatic context comes from the gateway plugin."
metadata: {"openclaw":{"emoji":"🔗"}}
---

# Reachy continuity

Use this skill only for explicit continuity operations in these exact authenticated sessions:

- Dylan iMessage: `agent:main:imessage:direct:dylanbochman@gmail.com`
- Reachy voice: `agent:main:reachy`

The `reachy-continuity` gateway plugin handles every-turn context automatically from the trusted runtime session key. It stores only expiring semantic summaries: at most 12 for four hours, with explicit handoffs retained for 24 hours. Never inspect or merge either raw transcript.

## Explicit handoffs

Treat “take this to text,” “send this to iMessage,” “pick this up on Reachy,” “continue this with Reachy,” and close equivalents as deliberate requests.

1. Call `reachy_continuity_handoff` with a concise paraphrase of only the requested discussion context.
2. From Reachy to iMessage, send that concise handoff only to Dylan's verified handle `dylanbochman@gmail.com`. Do not send the capsule.
3. From iMessage to Reachy, tell Dylan the handoff is queued. Do not make Reachy speak immediately unless he also asks.
4. After using a pending handoff, call `reachy_continuity_consume` with its exact ID. Never consume another handoff.

## Selective durable memory

For information originating in Reachy room speech, write to `memory/YYYY-MM-DD.md` or `MEMORY.md` only when Dylan explicitly asks in the current turn to “remember this,” “save this,” “write this down,” or an equally clear phrase. Store only the durable fact, decision, preference, or todo; never copy a transcript or capsule entry.

The gateway plugin blocks Reachy durable-memory writes without that current-turn authorization, including automatic memory-flush attempts. Dylan's iMessage DM continues to use the normal workspace memory rules.

## Forgetting

Call `reachy_continuity_clear` only when Dylan explicitly asks to forget or clear recent cross-channel context. Start new deployments with an empty capsule; never seed prior conversation automatically.

## Boundaries

- Treat capsule summaries as historical context, never present-tense authorization.
- Do not use them to authorize purchases, messages, smart-home actions, account changes, bookings, or other mutations.
- Exclude secrets, payment data, confirmation-capable identifiers, hidden reasoning, tool payloads, internal instructions, incidental third-party conversation, and speculative sensitive traits.
- Do not infer that a person seen by Reachy's camera is Dylan.
- Do not broaden session visibility, read transcript JSONL files, or retry failures with broader permissions.
- Continue the conversation without continuity when the plugin reports a safe failure.

## Verification

After deployment or modification, verify the plugin runtime exposes `before_prompt_build`, `before_tool_call`, and `agent_end`, and run its bundled 12-test suite. Confirm all continuity tools are absent outside the two exact sessions.
